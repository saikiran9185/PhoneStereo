package dev.saikiran.phonestereo;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.reflect.Method;
import java.net.Inet4Address;
import java.security.SecureRandom;
import java.util.ArrayDeque;
import java.util.Locale;

import rikka.shizuku.Shizuku;

/**
 * Phone side of PhoneStereo. Xiaomi only lets the shell user open the second back camera, so the
 * Start button runs StereoStream (packed inside this APK) as the shell user through Shizuku, in
 * Wi-Fi mode. The Mac finds the phone over Bonjour (_phonestereo._tcp) and must send the 4-digit
 * pairing code shown here before any frame is sent.
 */
public class MainActivity extends Activity {
    static final int PORT = 27183;
    static final String SERVICE_TYPE = "_phonestereo._tcp";
    static final int REQ = 7;

    // Kept static so the stream survives the activity being recreated.
    static Process proc;
    static String state = "Stopped";
    static String detail = "";
    static final ArrayDeque<String> logLines = new ArrayDeque<>();

    final Handler ui = new Handler(Looper.getMainLooper());
    TextView shizukuView, codeView, ipView, stateView, detailView, logView;
    Button startBtn, shizukuBtn, camBtn;
    NsdManager nsd;
    NsdManager.RegistrationListener nsdListener;

    final Shizuku.OnBinderReceivedListener binderReceived = () -> ui.post(this::refresh);
    final Shizuku.OnBinderDeadListener binderDead = () -> ui.post(this::refresh);
    final Shizuku.OnRequestPermissionResultListener permResult = (code, result) -> ui.post(this::refresh);

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        nsd = (NsdManager) getSystemService(NSD_SERVICE);
        setContentView(buildUi());
        Shizuku.addBinderReceivedListenerSticky(binderReceived);
        Shizuku.addBinderDeadListener(binderDead);
        Shizuku.addRequestPermissionResultListener(permResult);
        refresh();
    }

    @Override
    protected void onDestroy() {
        Shizuku.removeBinderReceivedListener(binderReceived);
        Shizuku.removeBinderDeadListener(binderDead);
        Shizuku.removeRequestPermissionResultListener(permResult);
        super.onDestroy();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    // ------------------------------------------------------------------ UI
    TextView text(float size, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setTextSize(size);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(0, dp(6), 0, dp(6));
        return t;
    }

    Button button(String label, int color) {
        Button btn = new Button(this);
        btn.setText(label);
        btn.setAllCaps(false);
        btn.setTextSize(18);
        btn.setTextColor(Color.BLACK);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(color);
        bg.setCornerRadius(dp(14));
        btn.setBackground(bg);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(58));
        lp.topMargin = dp(10);
        btn.setLayoutParams(lp);
        return btn;
    }

    int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    View buildUi() {
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(22), dp(36), dp(22), dp(22));
        col.setBackgroundColor(Color.rgb(11, 16, 32));

        TextView title = text(30, Color.rgb(0, 220, 255), true);
        title.setText("PhoneStereo");
        col.addView(title);
        TextView sub = text(14, Color.rgb(150, 160, 200), false);
        sub.setText("Main + ultra-wide camera depth, streamed to your Mac over Wi-Fi");
        col.addView(sub);

        shizukuView = text(15, Color.WHITE, false);
        col.addView(shizukuView);
        shizukuBtn = button("Open Shizuku", Color.rgb(255, 179, 71));
        shizukuBtn.setOnClickListener(v -> onShizukuButton());
        col.addView(shizukuBtn);

        TextView codeLabel = text(14, Color.rgb(150, 160, 200), false);
        codeLabel.setText("Pairing code (type it on the Mac once)");
        col.addView(codeLabel);
        codeView = text(46, Color.WHITE, true);
        codeView.setLetterSpacing(0.25f);
        codeView.setGravity(Gravity.CENTER);
        codeView.setOnLongClickListener(v -> { newCode(); return true; });
        col.addView(codeView);
        ipView = text(14, Color.rgb(150, 160, 200), false);
        ipView.setGravity(Gravity.CENTER);
        col.addView(ipView);

        stateView = text(22, Color.WHITE, true);
        col.addView(stateView);
        detailView = text(14, Color.rgb(200, 200, 220), false);
        col.addView(detailView);

        camBtn = button(camLabel(), Color.rgb(200, 190, 255));
        camBtn.setOnClickListener(v -> {
            if (proc != null) { log("stop the stream before switching camera"); return; }
            getSharedPreferences("ps", MODE_PRIVATE).edit().putString("cam", "1".equals(camId()) ? "4" : "1").apply();
            camBtn.setText(camLabel());
        });
        col.addView(camBtn);

        startBtn = button("Start stereo", Color.rgb(126, 224, 210));
        startBtn.setOnClickListener(v -> { if (proc == null) start(); else stop(); });
        col.addView(startBtn);

        logView = text(11, Color.rgb(140, 150, 180), false);
        logView.setTypeface(Typeface.MONOSPACE);
        col.addView(logView);

        ScrollView sv = new ScrollView(this);
        sv.addView(col);
        sv.setBackgroundColor(Color.rgb(11, 16, 32));
        return sv;
    }

    String code() {
        SharedPreferences p = getSharedPreferences("ps", MODE_PRIVATE);
        String c = p.getString("code", null);
        if (c == null) {
            c = String.format(Locale.US, "%04d", new SecureRandom().nextInt(10000));
            p.edit().putString("code", c).apply();
        }
        return c;
    }

    void newCode() {
        if (proc != null) return;
        getSharedPreferences("ps", MODE_PRIVATE).edit().remove("code").apply();
        log("new pairing code (the Mac will ask again)");
        refresh();
    }

    String wifiIp() {
        String any = null;
        try {
            for (java.net.NetworkInterface ni : java.util.Collections.list(java.net.NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (java.net.InetAddress a : java.util.Collections.list(ni.getInetAddresses())) {
                    if (!(a instanceof Inet4Address)) continue;
                    if (ni.getName().startsWith("wlan")) return a.getHostAddress();
                    if (any == null && !ni.getName().startsWith("tun")) any = a.getHostAddress();
                }
            }
        } catch (Throwable ignored) {
        }
        return any;
    }

    boolean shizukuRunning() {
        try { return Shizuku.pingBinder(); } catch (Throwable t) { return false; }
    }

    boolean shizukuGranted() {
        try { return Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED; }
        catch (Throwable t) { return false; }
    }

    void refresh() {
        boolean run = shizukuRunning(), ok = run && shizukuGranted();
        if (!run) {
            shizukuView.setText("✗ Shizuku is not running. Open Shizuku → Start (via Wireless debugging).");
            shizukuBtn.setText("Open Shizuku");
            shizukuBtn.setVisibility(View.VISIBLE);
        } else if (!ok) {
            shizukuView.setText("Shizuku is running. Allow PhoneStereo to use it.");
            shizukuBtn.setText("Allow Shizuku");
            shizukuBtn.setVisibility(View.VISIBLE);
        } else {
            shizukuView.setText("✓ Shizuku ready");
            shizukuBtn.setVisibility(View.GONE);
        }
        codeView.setText(code());
        String ip = wifiIp();
        ipView.setText(ip == null ? "No Wi-Fi" : "Phone " + ip + ":" + PORT + "   (long-press code = new code)");
        stateView.setText(state);
        stateView.setTextColor(state.startsWith("Streaming") ? Color.rgb(80, 220, 80)
                : state.startsWith("Error") ? Color.rgb(255, 93, 115) : Color.WHITE);
        detailView.setText(detail);
        startBtn.setText(proc == null ? "Start stereo" : "Stop");
        startBtn.setEnabled(ok || proc != null);
        startBtn.setAlpha(startBtn.isEnabled() ? 1f : 0.4f);
        StringBuilder sb = new StringBuilder();
        synchronized (logLines) { for (String l : logLines) sb.append(l).append('\n'); }
        logView.setText(sb);
        if (proc != null) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    void onShizukuButton() {
        if (!shizukuRunning()) {
            Intent i = getPackageManager().getLaunchIntentForPackage("moe.shizuku.privileged.api");
            if (i != null) startActivity(i);
        } else {
            Shizuku.requestPermission(REQ);
        }
    }

    static void log(String s) {
        synchronized (logLines) {
            logLines.addLast(s);
            while (logLines.size() > 14) logLines.removeFirst();
        }
    }

    // ------------------------------------------------------------------ stream
    static Process shell(String cmd) throws Exception {
        // Shizuku.newProcess is hidden in API 13 but still the simplest way to run a shell command.
        Method m = Shizuku.class.getDeclaredMethod("newProcess", String[].class, String[].class, String.class);
        m.setAccessible(true);
        return (Process) m.invoke(null, new String[]{"sh", "-c", cmd}, null, null);
    }

    // "4" = back logical camera (main + ultra-wide together), "1" = front (selfie) camera alone.
    String camId() { return getSharedPreferences("ps", MODE_PRIVATE).getString("cam", "4"); }

    String camLabel() { return "1".equals(camId()) ? "Camera: FRONT (selfie)  · tap to switch" : "Camera: BACK pair (main + wide)  · tap to switch"; }

    void start() {
        try {
            String apk = getApplicationInfo().sourceDir;
            // Stop any old streamer first, as its own command: in the same "sh -c" line the pattern
            // would also match (and kill) this very shell, whose command line names the class.
            shell("pkill -f '[p]honestereo.StereoStream'").waitFor();
            String cmd = "CLASSPATH=" + apk
                    + " app_process / dev.saikiran.phonestereo.StereoStream --camera " + camId()
                    + " --tcp " + PORT + " --token " + code()
                    + " --size 960x720 --quality 70 --focus 0 2>&1 | tee /data/local/tmp/phonestereo.log";
            proc = shell(cmd);
            state = "Starting cameras…";
            detail = "";
            log("start");
            final Process p = proc;
            new Thread(() -> pump(p), "pump").start();
        } catch (Throwable t) {
            proc = null;
            state = "Error: " + t.getClass().getSimpleName();
            detail = String.valueOf(t.getCause() != null ? t.getCause() : t);
        }
        refresh();
    }

    void pump(Process p) {
        try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
            String line;
            while ((line = r.readLine()) != null) {
                String s = line.trim();
                // MIUI prints a harmless theme-resources stack trace when the context is created.
                if (s.isEmpty() || s.startsWith("at ") || s.startsWith("...") || s.contains("Theme")
                        || s.contains("ErrnoException") || s.contains("FileNotFoundException")) continue;
                log(s.length() > 70 ? s.substring(0, 70) : s);
                if (s.startsWith("READY")) {
                    state = "Waiting for Mac…";
                    detail = "On the Mac, open PhoneStereo (Mac app). Keep this screen on.";
                    ui.post(this::advertise);
                } else if (s.startsWith("PAIRED")) {
                    state = "Streaming to " + s.substring(7);
                    detail = "";
                } else if (s.startsWith("phone:")) {
                    detail = s.substring(7);
                } else if (s.startsWith("REJECTED")) {
                    detail = "A device sent the wrong pairing code: " + s.substring(9);
                } else if (s.startsWith("waiting for next client")) {
                    state = "Waiting for Mac…";
                } else if (s.startsWith("session ended") && s.contains("camera")) {
                    detail = "Camera interrupted (screen off / another camera app?)";
                }
                ui.post(this::refresh);
            }
        } catch (Throwable t) {
            log("read error: " + t);
        }
        try { log("streamer exited, code " + p.waitFor()); } catch (Throwable t) { log("wait error: " + t); }
        ui.post(() -> {
            if (proc == p) {
                proc = null;
                state = "Stopped";
                unadvertise();
                refresh();
            }
        });
    }

    void stop() {
        Process p = proc;
        proc = null;
        state = "Stopped";
        detail = "";
        unadvertise();
        if (p != null) p.destroy();
        new Thread(() -> {
            try { shell("pkill -f '[p]honestereo.StereoStream'").waitFor(); } catch (Throwable ignored) { }
        }).start();
        log("stop");
        refresh();
    }

    void advertise() {
        if (nsdListener != null) return;
        NsdServiceInfo info = new NsdServiceInfo();
        info.setServiceName("PhoneStereo " + Build.MODEL);
        info.setServiceType(SERVICE_TYPE);
        info.setPort(PORT);
        nsdListener = new NsdManager.RegistrationListener() {
            @Override public void onServiceRegistered(NsdServiceInfo i) { log("visible to Mac as \"" + i.getServiceName() + "\""); ui.post(MainActivity.this::refresh); }
            @Override public void onRegistrationFailed(NsdServiceInfo i, int e) { log("Bonjour failed " + e + " (Mac can still use the IP)"); }
            @Override public void onServiceUnregistered(NsdServiceInfo i) { }
            @Override public void onUnregistrationFailed(NsdServiceInfo i, int e) { }
        };
        try { nsd.registerService(info, NsdManager.PROTOCOL_DNS_SD, nsdListener); }
        catch (Throwable t) { nsdListener = null; log("Bonjour error " + t); }
    }

    void unadvertise() {
        if (nsdListener == null) return;
        try { nsd.unregisterService(nsdListener); } catch (Throwable ignored) { }
        nsdListener = null;
    }
}
