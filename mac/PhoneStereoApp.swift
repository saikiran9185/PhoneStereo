// PhoneStereo for Mac: finds the phone app over Bonjour, pairs with its 4-digit code, asks macOS for
// camera access, and runs the vision modes (Python, in this project folder) as child processes, so the
// camera and local-network permissions belong to this app.
import ApplicationServices
import AVFoundation
import Network
import SwiftUI

struct Phone: Identifiable, Hashable {
    let name: String
    let endpoint: NWEndpoint
    var id: String { name }
}

enum Mode: String, CaseIterable, Identifiable {
    case air = "Air Buttons"
    case signs3 = "Sign Check · 3 cameras"
    case depth = "Live depth"
    case signsPhone = "Sign Check · phone only"
    case surface = "SurfaceTouch (projector wall)"
    case touch3d = "Touch 3D (stereo, phone in front)"
    case touch = "Touch Screen (phone at side)"
    case speed = "Sign Cue Test (research)"
    case link = "Link test"
    var id: String { rawValue }
    var script: String {
        switch self {
        case .depth: "viewer.py"
        case .air: "airbuttons.py"
        case .link: "linktest.py"
        case .speed: "speedtest.py"
        case .touch: "touchscreen.py"
        case .touch3d: "touch3d.py"
        case .surface: "run_surfacetouch.py"
        default: "signs.py"
        }
    }
    var extra: [String] { self == .signsPhone ? ["--no-mac"] : [] }
    var needsMacCamera: Bool { self == .signs3 || self == .air || self == .speed || self == .surface }
}

enum Link: String, CaseIterable, Identifiable {
    case wifi = "Wi-Fi"
    case usb = "USB cable"
    var id: String { rawValue }
}

@MainActor
final class Model: ObservableObject {
    @Published var phones: [Phone] = []
    @Published var selected: String?
    @Published var code = ""
    @Published var status = "Looking for the phone…"
    @Published var statusOK = false
    @Published var ip: String?
    @Published var busy = false
    @Published var running: Mode?
    @Published var log: [String] = []
    @Published var camera = AVCaptureDevice.authorizationStatus(for: .video)
    @Published var link: Link = .wifi
    @Published var usbSerial: String?

    let projectDir: String
    private var browser: NWBrowser?
    private var proc: Process?
    private let confURL = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".config/phonestereo/pairing.json")

    init() {
        projectDir = Bundle.main.object(forInfoDictionaryKey: "PSProjectDir") as? String
            ?? NSHomeDirectory() + "/Projects/PhoneStereo"
        browse()
        watchUSB()
    }

    // MARK: USB (adb from the Android SDK): the phone app's stream port is forwarded to 127.0.0.1
    private var adb: String { NSHomeDirectory() + "/Library/Android/sdk/platform-tools/adb" }

    private func runAdb(_ args: [String]) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: adb)
        p.arguments = args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        do { try p.run() } catch { return "" }
        p.waitUntilExit()
        return String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    }

    private func watchUSB() {
        Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { _ in
            DispatchQueue.global().async {
                let out = self.runAdbSync(["devices"])
                // USB devices are listed by serial; Wi-Fi ones contain ":" or "._adb-tls".
                let serial = out.split(separator: "\n").compactMap { line -> String? in
                    let f = line.split(separator: "\t")
                    guard f.count == 2, f[1] == "device", !f[0].contains(":"), !f[0].contains("._adb") else { return nil }
                    return String(f[0])
                }.first
                Task { @MainActor in
                    let was = self.usbSerial
                    self.usbSerial = serial
                    if serial != nil && was == nil && self.phones.isEmpty { self.link = .usb; self.pair() }
                    if serial == nil && self.link == .usb && self.running == nil {
                        self.status = "No phone on USB. Plug it in (USB debugging on) or switch to Wi-Fi."
                        self.statusOK = false
                        self.ip = nil
                    }
                }
            }
        }.fire()
    }

    nonisolated func runAdbSync(_ args: [String]) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: NSHomeDirectory() + "/Library/Android/sdk/platform-tools/adb")
        p.arguments = args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        do { try p.run() } catch { return "" }
        p.waitUntilExit()
        return String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    }

    func setLink(_ l: Link) {
        link = l
        ip = nil
        statusOK = false
        if code.isEmpty, let any = (loadConf()["codes"] as? [String: String])?.values.first { code = any }
        pair()
    }

    // MARK: saved codes (same file as connect.py)
    private func loadConf() -> [String: Any] {
        guard let d = try? Data(contentsOf: confURL),
              let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return [:] }
        return j
    }

    private func savedCode(_ name: String) -> String? {
        (loadConf()["codes"] as? [String: String])?[name]
    }

    private func saveCode(_ name: String, _ code: String, ip: String) {
        var c = loadConf()
        var codes = c["codes"] as? [String: String] ?? [:]
        codes[name] = code
        c["codes"] = codes
        c["last_ip"] = ip
        try? FileManager.default.createDirectory(at: confURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let d = try? JSONSerialization.data(withJSONObject: c, options: [.prettyPrinted]) {
            try? d.write(to: confURL)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: confURL.path)
        }
    }

    // MARK: Bonjour
    func browse() {
        let b = NWBrowser(for: .bonjour(type: "_phonestereo._tcp", domain: nil), using: .tcp)
        b.browseResultsChangedHandler = { results, _ in
            let found = results.compactMap { r -> Phone? in
                if case let .service(name, _, _, _) = r.endpoint { return Phone(name: name, endpoint: r.endpoint) }
                return nil
            }.sorted { $0.name < $1.name }
            Task { @MainActor in self.updatePhones(found) }
        }
        b.stateUpdateHandler = { state in
            Task { @MainActor in
                if case let .failed(e) = state { self.status = "Network search failed: \(e)" }
                if case let .waiting(e) = state { self.status = "Allow Local Network for PhoneStereo (\(e))" }
            }
        }
        b.start(queue: .main)
        browser = b
    }

    private func updatePhones(_ found: [Phone]) {
        phones = found
        if found.isEmpty {
            if link == .usb { return }
            if running == nil { status = "Looking for the phone… open PhoneStereo on the phone and tap Start stereo." }
            statusOK = false
            ip = nil
            return
        }
        if selected == nil || !found.contains(where: { $0.name == selected }) {
            selected = found[0].name
            code = savedCode(found[0].name) ?? ""
            ip = nil
            statusOK = false
            if link == .usb { return }
            if !code.isEmpty { pair() } else { status = "Found \(found[0].name). Type the code shown on the phone." }
        }
    }

    func select(_ name: String) {
        selected = name
        code = savedCode(name) ?? ""
        ip = nil
        statusOK = false
        if !code.isEmpty { pair() }
    }

    // MARK: pairing check: connect, send "PS <code>", expect the stream header magic
    func pair() {
        let endpoint: NWEndpoint
        let name: String
        if link == .usb {
            guard let serial = usbSerial else {
                status = "No phone on USB. Plug it in (USB debugging on) or switch to Wi-Fi."
                return
            }
            let fwd = runAdb(["-s", serial, "forward", "tcp:27183", "tcp:27183"])
            if !fwd.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("27183") && !fwd.isEmpty {
                status = "adb forward failed: \(fwd)"
                return
            }
            endpoint = .hostPort(host: "127.0.0.1", port: 27183)
            name = selected ?? "usb:\(serial)"
        } else {
            guard let sel = selected, let phone = phones.first(where: { $0.name == sel }) else { return }
            endpoint = phone.endpoint
            name = sel
        }
        if code.isEmpty, let saved = savedCode(name) { code = saved }
        let c = code.trimmingCharacters(in: .whitespaces)
        guard c.count == 4, Int(c) != nil else { status = "The code is 4 digits."; return }
        busy = true
        status = "Pairing with \(name)…"
        let params = NWParameters.tcp
        if let ipOpt = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options { ipOpt.version = .v4 }
        let conn = NWConnection(to: endpoint, using: params)
        let linkName = link == .usb ? "USB" : "Wi-Fi"
        var finished = false
        let finish: @Sendable (String?, String?) -> Void = { err, addr in
            Task { @MainActor in
                if finished { return }
                finished = true
                conn.cancel()
                self.busy = false
                if let err {
                    self.status = err
                    self.statusOK = false
                } else if let addr {
                    self.ip = addr
                    self.statusOK = true
                    self.status = "Paired with \(name) over \(linkName) · \(addr)"
                    self.saveCode(name, c, ip: addr)
                }
            }
        }
        conn.stateUpdateHandler = { state in
            switch state {
            case .ready:
                var addr: String?
                if case let .hostPort(host, _) = conn.currentPath?.remoteEndpoint {
                    addr = "\(host)".components(separatedBy: "%")[0]
                }
                if addr == "::1" || addr == "localhost" { addr = "127.0.0.1" }
                conn.send(content: "PS \(c)\n".data(using: .utf8), completion: .contentProcessed { _ in })
                conn.receive(minimumIncompleteLength: 8, maximumLength: 8) { data, _, _, _ in
                    guard let d = data, d.count >= 4 else { finish("No answer from the phone app.", nil); return }
                    if d.starts(with: Array("ERR".utf8)) { finish("Wrong code. Check the 4 digits on the phone.", nil); return }
                    let magic = d.prefix(4).reduce(UInt32(0)) { $0 << 8 | UInt32($1) }
                    finish(magic == 0x5053_5431 ? nil : "Unexpected reply from the phone.", addr)
                }
            case let .failed(e): finish("Can't reach the phone: \(e)", nil)
            case let .waiting(e): finish("Can't reach the phone (\(e)). Allow Local Network for PhoneStereo.", nil)
            default: break
            }
        }
        conn.start(queue: .main)
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { finish("Pairing timed out.", nil) }
    }

    // MARK: camera
    func askCamera(then: @escaping @MainActor () -> Void) {
        camera = AVCaptureDevice.authorizationStatus(for: .video)
        if camera == .notDetermined {
            AVCaptureDevice.requestAccess(for: .video) { _ in
                Task { @MainActor in
                    self.camera = AVCaptureDevice.authorizationStatus(for: .video)
                    then()
                }
            }
        } else {
            then()
        }
    }

    func openCameraSettings() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera")!)
    }

    // MARK: run a mode
    func launch(_ mode: Mode) {
        guard let ip, running == nil else { return }
        if mode.needsMacCamera {
            askCamera { self.start(mode, ip: ip) }
        } else {
            start(mode, ip: ip)
        }
    }

    private func start(_ mode: Mode, ip: String) {
        var args = [mode.script, "--host", ip, "--token", code.trimmingCharacters(in: .whitespaces)] + mode.extra
        if link == .usb && mode != .link { _ = runAdb(["-s", usbSerial ?? "", "forward", "tcp:27183", "tcp:27183"]) }
        if mode == .surface && link == .usb {
            // Zoom the phone sensor onto the wall (the phone stays by the projector). Needs the USB streamer
            // (./build_streamer.sh); it closes the phone app, which would restart its own un-zoomed stream.
            args.append("--zoom")
        }
        if (mode == .touch || mode == .touch3d || mode == .surface) && !AXIsProcessTrusted() {
            // Live touch posts mouse clicks and arrow keys: macOS asks for Accessibility once.
            let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(opts)
            append("Touch Screen: allow PhoneStereo in System Settings > Privacy & Security > Accessibility for LIVE mode (calibration and test work without it).")
        }
        if mode.needsMacCamera && camera != .authorized && (mode == .air || mode == .touch) {
            append("Air Buttons needs the Mac camera. Allow it in Settings, then run again.")
            return
        }
        if mode.needsMacCamera && camera != .authorized {
            args.append("--no-mac")
            append("Mac camera not allowed: running with the phone only. Allow it in Settings, then run again.")
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: projectDir + "/.venv/bin/python")
        p.arguments = args
        p.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "1"     // this app already asked for the camera
        env["GLOG_minloglevel"] = "2"
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            guard !d.isEmpty, let s = String(data: d, encoding: .utf8) else { return }
            Task { @MainActor in
                for line in s.split(separator: "\n") where !Model.noise(line) { self.append(String(line)) }
            }
        }
        p.terminationHandler = { proc in
            Task { @MainActor in
                pipe.fileHandleForReading.readabilityHandler = nil
                self.append("— \(mode.rawValue) ended (code \(proc.terminationStatus))")
                self.running = nil
                self.proc = nil
            }
        }
        do {
            try p.run()
            proc = p
            running = mode
            append("▶ \(mode.rawValue) → \(ip)")
        } catch {
            append("Could not start Python: \(error.localizedDescription)")
        }
    }

    func stop() { proc?.terminate() }

    nonisolated static func noise(_ l: Substring) -> Bool {
        l.hasPrefix("W0000") || l.hasPrefix("I0000") || l.hasPrefix("INFO:") || l.contains("landmark_projection")
            || l.contains("inference_feedback") || l.trimmingCharacters(in: .whitespaces).isEmpty
    }

    func append(_ s: String) {
        log.append(s)
        if log.count > 400 { log.removeFirst(log.count - 400) }
    }
}

struct ContentView: View {
    @EnvironmentObject var m: Model

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Image(systemName: "camera.metering.matrix").font(.system(size: 26)).foregroundStyle(.cyan)
                VStack(alignment: .leading, spacing: 2) {
                    Text("PhoneStereo").font(.title2.bold())
                    Text("Phone main + ultra-wide depth, with the Mac camera as a third view")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }

            GroupBox("Phone") {
                VStack(alignment: .leading, spacing: 10) {
                    Picker("Connection", selection: Binding(get: { m.link }, set: { m.setLink($0) })) {
                        ForEach(Link.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    Text(m.link == .usb
                         ? (m.usbSerial.map { "USB: phone \($0) connected · steadier (no Wi-Fi stalls)" } ?? "USB: no phone found. Plug in the cable, allow USB debugging.")
                         : "Wi-Fi: phone and Mac on the same network")
                        .font(.caption).foregroundStyle(.secondary)
                    if m.link == .usb {
                        HStack {
                            Text("Code")
                            TextField("4 digits", text: $m.code)
                                .textFieldStyle(.roundedBorder).frame(width: 90)
                                .font(.system(.body, design: .monospaced))
                                .onSubmit { m.pair() }
                            Button(m.statusOK ? "Re-pair" : "Pair") { m.pair() }.disabled(m.busy || m.usbSerial == nil)
                        }
                    } else if m.phones.isEmpty {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text("Open PhoneStereo on the phone and tap Start stereo.").foregroundStyle(.secondary)
                        }
                    } else {
                        Picker("Phone", selection: Binding(get: { m.selected ?? "" }, set: { m.select($0) })) {
                            ForEach(m.phones) { Text($0.name).tag($0.name) }
                        }
                        HStack {
                            Text("Code")
                            TextField("4 digits", text: $m.code)
                                .textFieldStyle(.roundedBorder).frame(width: 90)
                                .font(.system(.body, design: .monospaced))
                                .onSubmit { m.pair() }
                            Button(m.statusOK ? "Re-pair" : "Pair") { m.pair() }.disabled(m.busy)
                        }
                    }
                    Label(m.status, systemImage: m.statusOK ? "checkmark.circle.fill" : "info.circle")
                        .foregroundStyle(m.statusOK ? .green : .secondary)
                        .font(.callout)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(4)
            }

            GroupBox("Mac camera") {
                HStack {
                    switch m.camera {
                    case .authorized: Label("Allowed", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    case .notDetermined: Text("Will ask when you start the 3-camera game.").foregroundStyle(.secondary)
                    default:
                        Label("Blocked", systemImage: "xmark.octagon.fill").foregroundStyle(.red)
                        Spacer()
                        Button("Open Camera settings") { m.openCameraSettings() }
                    }
                    Spacer()
                }
                .padding(4)
            }

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                ForEach(Mode.allCases) { mode in
                    Button { m.launch(mode) } label: {
                        Text(mode.rawValue).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(mode == .surface ? .purple : mode == .touch || mode == .touch3d ? .blue : mode == .air ? .green : mode == .speed ? .pink : mode == .depth ? .cyan : mode == .link ? .gray : .orange)
                    .controlSize(.large)
                    .disabled(m.ip == nil || m.running != nil)
                }
            }
            if let r = m.running {
                HStack {
                    ProgressView().controlSize(.small)
                    Text("\(r.rawValue) is running in its own window.")
                    Spacer()
                    Button("Stop", role: .destructive) { m.stop() }
                }
            }

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 1) {
                        ForEach(Array(m.log.enumerated()), id: \.offset) { i, line in
                            Text(line).font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(.secondary).textSelection(.enabled).id(i)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                }
                .background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .textBackgroundColor).opacity(0.5)))
                .onChange(of: m.log.count) { _, n in proxy.scrollTo(n - 1, anchor: .bottom) }
            }
        }
        .padding(18)
        .frame(minWidth: 620, minHeight: 560)
        .onAppear { m.camera = AVCaptureDevice.authorizationStatus(for: .video) }
    }
}

@main
struct PhoneStereoApp: App {
    @StateObject var model = Model()
    var body: some Scene {
        WindowGroup("PhoneStereo") { ContentView().environmentObject(model) }
            .windowResizability(.contentMinSize)
    }
}
