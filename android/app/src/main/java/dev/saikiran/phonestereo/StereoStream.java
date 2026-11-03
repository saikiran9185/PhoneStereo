package dev.saikiran.phonestereo;

import android.content.AttributionSource;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.pm.ApplicationInfo;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.camera2.*;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.media.Image;
import android.media.ImageReader;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.util.Size;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;

// Streams the two physical cameras behind the logical multi-camera to the Mac.
// Runs via app_process as the shell user (Xiaomi hides aux cameras from normal apps).
// USB/adb mode (default): adb forward tcp:27183 localabstract:phonestereo, then connect.
// Wi-Fi mode (--tcp PORT --token CODE): listens on the network; the Mac must first send the line
// "PS <code>\n" or it is dropped, so nobody else on the Wi-Fi can open the camera.
// The PhoneStereo app starts this through Shizuku (shell user) in Wi-Fi mode.
//
// Wire format (big-endian):
//   header: int 'PST1', int len, UTF-8 JSON (calibration + stream info)
//   frame:  int 'FRM1', byte cam (0 = first physical id, 1 = second), long sensorTimestampNs,
//           long phoneNanoTime, short w, short h, int jpegLen, jpeg bytes
public class StereoStream {
    static final int MAGIC_HEADER = 0x50535431;
    static final int MAGIC_FRAME = 0x46524D31;

    static String logicalId = "4";
    static int width = 1280, height = 960, quality = 80;
    static float focusDiopters = 1.5f; // 0 = continuous autofocus
    static int tcpPort = 0;
    static String token = null;

    static class FakeContext extends ContextWrapper {
        FakeContext(Context base) { super(base); }
        @Override public String getPackageName() { return "com.android.shell"; }
        @Override public String getOpPackageName() { return "com.android.shell"; }
        @Override public AttributionSource getAttributionSource() {
            return new AttributionSource.Builder(2000).setPackageName("com.android.shell").build();
        }
        @Override public Context getApplicationContext() { return this; }
    }

    static Context systemContext() throws Exception {
        Class<?> atc = Class.forName("android.app.ActivityThread");
        Constructor<?> ctor = atc.getDeclaredConstructor();
        ctor.setAccessible(true);
        Object at = ctor.newInstance();
        Field cur = atc.getDeclaredField("sCurrentActivityThread");
        cur.setAccessible(true);
        cur.set(null, at);
        try {
            Field sys = atc.getDeclaredField("mSystemThread");
            sys.setAccessible(true);
            sys.setBoolean(at, true);
            Class<?> abd = Class.forName("android.app.ActivityThread$AppBindData");
            Constructor<?> abdCtor = abd.getDeclaredConstructor();
            abdCtor.setAccessible(true);
            Object bind = abdCtor.newInstance();
            ApplicationInfo ai = new ApplicationInfo();
            ai.packageName = "com.android.shell";
            Field aif = abd.getDeclaredField("appInfo");
            aif.setAccessible(true);
            aif.set(bind, ai);
            Field mb = atc.getDeclaredField("mBoundApplication");
            mb.setAccessible(true);
            mb.set(at, bind);
        } catch (Throwable ignored) {
        }
        return (Context) atc.getDeclaredMethod("getSystemContext").invoke(at);
    }

    static JSONArray arr(float[] a) throws Exception {
        JSONArray j = new JSONArray();
        if (a != null) for (float f : a) j.put((double) f);
        return j;
    }

    static JSONArray rect(Rect r) {
        JSONArray j = new JSONArray();
        if (r != null) { j.put(r.left); j.put(r.top); j.put(r.width()); j.put(r.height()); }
        return j;
    }

    static JSONObject header(CameraManager cm, List<String> phys) throws Exception {
        JSONObject h = new JSONObject();
        h.put("logicalId", logicalId);
        h.put("width", width);
        h.put("height", height);
        h.put("focusDiopters", (double) focusDiopters);
        CameraCharacteristics lc = cm.getCameraCharacteristics(logicalId);
        Integer sync = lc.get(CameraCharacteristics.LOGICAL_MULTI_CAMERA_SENSOR_SYNC_TYPE);
        h.put("syncType", sync == null ? "unknown" : (sync == 1 ? "CALIBRATED" : "APPROXIMATE"));
        JSONArray cams = new JSONArray();
        for (String id : phys) {
            CameraCharacteristics c = cm.getCameraCharacteristics(id);
            JSONObject o = new JSONObject();
            o.put("id", id);
            o.put("intrinsics", arr(c.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION)));
            o.put("distortion", arr(c.get(CameraCharacteristics.LENS_DISTORTION)));
            o.put("poseRotation", arr(c.get(CameraCharacteristics.LENS_POSE_ROTATION)));
            o.put("poseTranslation", arr(c.get(CameraCharacteristics.LENS_POSE_TRANSLATION)));
            o.put("focalLengths", arr(c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)));
            o.put("preCorrectionActiveArray", rect(c.get(CameraCharacteristics.SENSOR_INFO_PRE_CORRECTION_ACTIVE_ARRAY_SIZE)));
            o.put("activeArray", rect(c.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE)));
            Integer orient = c.get(CameraCharacteristics.SENSOR_ORIENTATION);
            o.put("sensorOrientation", orient == null ? -1 : orient);
            cams.put(o);
        }
        h.put("cameras", cams);
        return h;
    }

    // YUV_420_888 -> NV21, bulk-copying planes into arrays first (per-pixel ByteBuffer.get is slow).
    static final class Encoder {
        byte[] nv21, ub, vb;
        final ByteArrayOutputStream jpeg = new ByteArrayOutputStream(256 * 1024);

        byte[] encode(Image img) {
            int w = img.getWidth(), h = img.getHeight();
            if (nv21 == null || nv21.length != w * h * 3 / 2) nv21 = new byte[w * h * 3 / 2];
            Image.Plane[] p = img.getPlanes();
            ByteBuffer y = p[0].getBuffer();
            int yrs = p[0].getRowStride();
            if (yrs == w) {
                y.get(nv21, 0, w * h);
            } else {
                for (int r = 0; r < h; r++) {
                    y.position(r * yrs);
                    y.get(nv21, r * w, w);
                }
            }
            ByteBuffer u = p[1].getBuffer(), v = p[2].getBuffer();
            if (ub == null || ub.length < u.remaining()) ub = new byte[u.remaining()];
            if (vb == null || vb.length < v.remaining()) vb = new byte[v.remaining()];
            int ul = u.remaining(), vl = v.remaining();
            u.get(ub, 0, ul);
            v.get(vb, 0, vl);
            int crs = p[1].getRowStride(), cps = p[1].getPixelStride();
            int pos = w * h;
            for (int r = 0; r < h / 2; r++) {
                int base = r * crs;
                for (int c = 0; c < w / 2; c++) {
                    int i = base + c * cps;
                    nv21[pos++] = vb[i];
                    nv21[pos++] = ub[i];
                }
            }
            jpeg.reset();
            new YuvImage(nv21, ImageFormat.NV21, w, h, null).compressToJpeg(new Rect(0, 0, w, h), quality, jpeg);
            return jpeg.toByteArray();
        }
    }

    static byte[] packet(int cam, long ts, int w, int h, byte[] jpg) {
        ByteBuffer b = ByteBuffer.allocate(4 + 1 + 8 + 8 + 2 + 2 + 4 + jpg.length);
        b.putInt(MAGIC_FRAME).put((byte) cam).putLong(ts).putLong(System.nanoTime())
                .putShort((short) w).putShort((short) h).putInt(jpg.length).put(jpg);
        return b.array();
    }

    static void serve(CameraManager cm, OutputStream rawOut) throws Exception {
        List<String> phys = new ArrayList<>(cm.getCameraCharacteristics(logicalId).getPhysicalCameraIds());
        Collections.sort(phys);
        // Not a multi-camera (e.g. the front camera): stream that one camera alone as cam 0.
        final boolean single = phys.size() < 2;
        if (single) { phys.clear(); phys.add(logicalId); }
        final int n = single ? 1 : 2;

        DataOutputStream out = new DataOutputStream(new BufferedOutputStream(rawOut, 1 << 17));
        byte[] hj = header(cm, phys).toString().getBytes(StandardCharsets.UTF_8);
        out.writeInt(MAGIC_HEADER);
        out.writeInt(hj.length);
        out.write(hj);
        out.flush();

        final ArrayBlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(8);
        final AtomicLong dropped = new AtomicLong();
        final long[] frames = new long[2];
        final long[] encNs = new long[2];
        final String[] camErr = new String[1];

        HandlerThread camThread = new HandlerThread("cam");
        camThread.start();
        Handler camHandler = new Handler(camThread.getLooper());
        HandlerThread[] encThreads = new HandlerThread[2];
        ImageReader[] readers = new ImageReader[2];
        for (int k = 0; k < n; k++) {
            final int cam = k;
            encThreads[k] = new HandlerThread("enc" + k);
            encThreads[k].start();
            final Encoder enc = new Encoder();
            readers[k] = ImageReader.newInstance(width, height, ImageFormat.YUV_420_888, 3);
            readers[k].setOnImageAvailableListener(r -> {
                Image img = r.acquireLatestImage();
                if (img == null) return;
                try {
                    long t = System.nanoTime();
                    byte[] jpg = enc.encode(img);
                    encNs[cam] += System.nanoTime() - t;
                    frames[cam]++;
                    byte[] p = packet(cam, img.getTimestamp(), img.getWidth(), img.getHeight(), jpg);
                    while (!queue.offer(p)) {
                        queue.poll();
                        dropped.incrementAndGet();
                    }
                } catch (Throwable e) {
                    camErr[0] = "encode: " + e;
                } finally {
                    img.close();
                }
            }, new Handler(encThreads[k].getLooper()));
        }

        CountDownLatch opened = new CountDownLatch(1);
        final CameraDevice[] dev = new CameraDevice[1];
        cm.openCamera(logicalId, new CameraDevice.StateCallback() {
            @Override public void onOpened(CameraDevice d) { dev[0] = d; opened.countDown(); }
            @Override public void onDisconnected(CameraDevice d) { camErr[0] = "camera disconnected (another app took it?)"; d.close(); opened.countDown(); }
            @Override public void onError(CameraDevice d, int e) { camErr[0] = "camera error " + e; d.close(); opened.countDown(); }
        }, camHandler);
        opened.await(10, TimeUnit.SECONDS);
        CameraCaptureSession session = null;
        try {
            if (dev[0] == null) throw new IllegalStateException("could not open camera: " + camErr[0]);
            List<OutputConfiguration> outs = new ArrayList<>();
            for (int k = 0; k < n; k++) {
                OutputConfiguration oc = new OutputConfiguration(readers[k].getSurface());
                if (!single) oc.setPhysicalCameraId(phys.get(k));
                outs.add(oc);
            }
            CountDownLatch configured = new CountDownLatch(1);
            final CameraCaptureSession[] sess = new CameraCaptureSession[1];
            dev[0].createCaptureSession(new SessionConfiguration(SessionConfiguration.SESSION_REGULAR,
                    outs, camHandler::post, new CameraCaptureSession.StateCallback() {
                        @Override public void onConfigured(CameraCaptureSession s) { sess[0] = s; configured.countDown(); }
                        @Override public void onConfigureFailed(CameraCaptureSession s) { camErr[0] = "session configure failed"; configured.countDown(); }
                    }));
            configured.await(10, TimeUnit.SECONDS);
            session = sess[0];
            if (session == null) throw new IllegalStateException(camErr[0]);

            CaptureRequest.Builder rq = dev[0].createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
            for (int k = 0; k < n; k++) rq.addTarget(readers[k].getSurface());
            if (focusDiopters > 0) {
                // A fixed focus keeps the main lens from "breathing" (zooming slightly while
                // autofocus hunts), which would shift pixels and corrupt the disparity.
                rq.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_OFF);
                rq.set(CaptureRequest.LENS_FOCUS_DISTANCE, focusDiopters);
            }
            session.setRepeatingRequest(rq.build(), null, camHandler);
            System.out.println("streaming " + phys + " at " + width + "x" + height + ", focus " +
                    (focusDiopters > 0 ? (focusDiopters + " dpt (" + Math.round(100 / focusDiopters) + " cm)") : "auto"));

            long lastLog = System.nanoTime();
            long[] lastFrames = new long[2];
            while (true) {
                byte[] p = queue.poll(500, TimeUnit.MILLISECONDS);
                if (camErr[0] != null) throw new IllegalStateException(camErr[0]);
                if (p != null) out.write(p);
                if (queue.isEmpty()) out.flush();
                long now = System.nanoTime();
                if (now - lastLog > 5_000_000_000L) {
                    double s = (now - lastLog) / 1e9;
                    System.out.printf(Locale.US, "phone: cam0 %.1f fps (enc %.1f ms)  cam1 %.1f fps (enc %.1f ms)  dropped %d%n",
                            (frames[0] - lastFrames[0]) / s, frames[0] == 0 ? 0 : encNs[0] / 1e6 / frames[0],
                            (frames[1] - lastFrames[1]) / s, frames[1] == 0 ? 0 : encNs[1] / 1e6 / frames[1],
                            dropped.get());
                    lastFrames[0] = frames[0];
                    lastFrames[1] = frames[1];
                    lastLog = now;
                }
            }
        } finally {
            try { if (session != null) session.close(); } catch (Throwable ignored) { }
            try { if (dev[0] != null) dev[0].close(); } catch (Throwable ignored) { }
            for (ImageReader r : readers) if (r != null) r.close();
            for (HandlerThread t : encThreads) if (t != null) t.quitSafely();
            camThread.quitSafely();
        }
    }

    public static void main(String[] args) throws Exception {
        for (int i = 0; i + 1 < args.length; i += 2) {
            switch (args[i]) {
                case "--camera": logicalId = args[i + 1]; break;
                case "--size": {
                    String[] wh = args[i + 1].split("x");
                    width = Integer.parseInt(wh[0]);
                    height = Integer.parseInt(wh[1]);
                    break;
                }
                case "--quality": quality = Integer.parseInt(args[i + 1]); break;
                case "--focus": focusDiopters = Float.parseFloat(args[i + 1]); break;
                case "--tcp": tcpPort = Integer.parseInt(args[i + 1]); break;
                case "--token": token = args[i + 1]; break;
            }
        }
        Looper.prepareMainLooper();
        Constructor<CameraManager> cmCtor = CameraManager.class.getDeclaredConstructor(Context.class);
        cmCtor.setAccessible(true);
        CameraManager cm = cmCtor.newInstance(new FakeContext(systemContext()));

        if (tcpPort > 0) {
            serveTcp(cm);
            return;
        }
        LocalServerSocket server = new LocalServerSocket("phonestereo");
        System.out.println("READY localabstract:phonestereo (camera " + logicalId + ")");
        while (true) {
            LocalSocket client = server.accept();
            System.out.println("client connected");
            try {
                serve(cm, client.getOutputStream());
            } catch (Throwable t) {
                System.out.println("session ended: " + t);
            } finally {
                try { client.close(); } catch (Throwable ignored) { }
            }
            System.out.println("waiting for next client");
        }
    }

    // Reads one line (max 64 bytes) from the client: the pairing handshake.
    static String readLine(InputStream in) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 64; i++) {
            int c = in.read();
            if (c < 0 || c == '\n') break;
            sb.append((char) c);
        }
        return sb.toString().trim();
    }

    static void serveTcp(CameraManager cm) throws Exception {
        ServerSocket server = new ServerSocket();
        server.setReuseAddress(true);
        server.bind(new InetSocketAddress(tcpPort));
        System.out.println("READY tcp:" + tcpPort + " (camera " + logicalId + ")");
        while (true) {
            Socket client = server.accept();
            String who = client.getInetAddress().getHostAddress();
            try {
                client.setSoTimeout(5000);
                String hello = readLine(client.getInputStream());
                if (token != null && !hello.equals("PS " + token)) {
                    System.out.println("REJECTED " + who + " (wrong pairing code)");
                    client.getOutputStream().write("ERR wrong code\n".getBytes(StandardCharsets.UTF_8));
                    continue;
                }
                client.setSoTimeout(0);
                client.setTcpNoDelay(true);
                client.setSendBufferSize(1 << 20);
                System.out.println("PAIRED " + who);
                serve(cm, client.getOutputStream());
            } catch (Throwable t) {
                System.out.println("session ended: " + t);
            } finally {
                try { client.close(); } catch (Throwable ignored) { }
            }
            System.out.println("waiting for next client");
        }
    }
}
