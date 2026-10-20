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
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.util.Size;

import java.io.FileOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.nio.ByteBuffer;
import java.util.*;
import java.util.concurrent.*;

// Can the macro camera (3) run at the same time as the logical main+ultra-wide pair (4)?
// Runs via app_process as the shell user, like StereoProbe. Saves one JPEG per stream.
public class TriProbe {
    static final String OUT = "/data/local/tmp/stereo";

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
        } catch (Throwable t) {
            System.out.println("note: app-info workaround skipped: " + t);
        }
        return (Context) atc.getDeclaredMethod("getSystemContext").invoke(at);
    }

    static byte[] toNv21(Image img) {
        int w = img.getWidth(), h = img.getHeight();
        byte[] out = new byte[w * h * 3 / 2];
        Image.Plane[] p = img.getPlanes();
        int pos = 0;
        ByteBuffer y = p[0].getBuffer();
        for (int r = 0; r < h; r++) {
            y.position(r * p[0].getRowStride());
            y.get(out, pos, w);
            pos += w;
        }
        ByteBuffer u = p[1].getBuffer(), v = p[2].getBuffer();
        int rs = p[1].getRowStride(), ps = p[1].getPixelStride();
        for (int r = 0; r < h / 2; r++) {
            for (int c = 0; c < w / 2; c++) {
                int i = r * rs + c * ps;
                out[pos++] = v.get(i);
                out[pos++] = u.get(i);
            }
        }
        return out;
    }

    static void saveJpeg(Image img, String path) throws Exception {
        YuvImage yuv = new YuvImage(toNv21(img), ImageFormat.NV21, img.getWidth(), img.getHeight(), null);
        try (FileOutputStream fo = new FileOutputStream(path)) {
            yuv.compressToJpeg(new Rect(0, 0, img.getWidth(), img.getHeight()), 92, fo);
        }
    }

    static Size pick(CameraCharacteristics c) {
        List<Size> s = Arrays.asList(((StreamConfigurationMap) c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP))
                .getOutputSizes(ImageFormat.YUV_420_888));
        for (Size w : new Size[]{ new Size(1280, 960), new Size(1024, 768), new Size(800, 600), new Size(640, 480) })
            if (s.contains(w)) return w;
        return s.get(s.size() - 1);
    }

    // per-stream counters, indexed by label
    static final Map<String, long[]> counts = new ConcurrentHashMap<>();
    static final Map<String, Boolean> saved = new ConcurrentHashMap<>();
    static final List<String> events = Collections.synchronizedList(new ArrayList<>());
    static final long T0 = System.nanoTime();

    static String now() { return String.format("%.2fs", (System.nanoTime() - T0) / 1e9); }

    static ImageReader reader(String label, Size size, Handler h) {
        ImageReader r = ImageReader.newInstance(size.getWidth(), size.getHeight(), ImageFormat.YUV_420_888, 4);
        counts.put(label, new long[1]);
        r.setOnImageAvailableListener(rd -> {
            Image img = rd.acquireLatestImage();
            if (img == null) return;
            try {
                long n = ++counts.get(label)[0];
                if (n == 30 && !saved.containsKey(label)) {
                    saveJpeg(img, OUT + "/tri_" + label + ".jpg");
                    saved.put(label, true);
                }
            } catch (Exception e) {
                System.out.println("save error " + label + ": " + e);
            } finally {
                img.close();
            }
        }, h);
        return r;
    }

    static CameraDevice open(CameraManager cm, String id, Handler h) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        CameraDevice[] dev = new CameraDevice[1];
        String[] err = new String[1];
        cm.openCamera(id, new CameraDevice.StateCallback() {
            @Override public void onOpened(CameraDevice d) { dev[0] = d; done.countDown(); }
            @Override public void onDisconnected(CameraDevice d) {
                events.add(now() + " camera " + id + " DISCONNECTED (evicted)");
                err[0] = "disconnected"; d.close(); done.countDown();
            }
            @Override public void onError(CameraDevice d, int e) {
                events.add(now() + " camera " + id + " ERROR " + e);
                err[0] = "error " + e; d.close(); done.countDown();
            }
        }, h);
        done.await(8, TimeUnit.SECONDS);
        if (dev[0] == null) throw new RuntimeException("open " + id + " failed: " + err[0]);
        events.add(now() + " camera " + id + " opened");
        return dev[0];
    }

    static void start(CameraDevice d, List<OutputConfiguration> outs, List<ImageReader> rs, Executor ex, Handler h) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        CameraCaptureSession[] s = new CameraCaptureSession[1];
        d.createCaptureSession(new SessionConfiguration(SessionConfiguration.SESSION_REGULAR, outs, ex,
                new CameraCaptureSession.StateCallback() {
                    @Override public void onConfigured(CameraCaptureSession x) { s[0] = x; done.countDown(); }
                    @Override public void onConfigureFailed(CameraCaptureSession x) { done.countDown(); }
                }));
        done.await(8, TimeUnit.SECONDS);
        if (s[0] == null) throw new RuntimeException("session on " + d.getId() + " failed");
        CaptureRequest.Builder rq = d.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
        for (ImageReader r : rs) rq.addTarget(r.getSurface());
        s[0].setRepeatingRequest(rq.build(), null, h);
        events.add(now() + " camera " + d.getId() + " streaming");
    }

    public static void main(String[] args) throws Exception {
        String order = args.length > 0 ? args[0] : "pair-first";
        Looper.prepareMainLooper();
        Constructor<CameraManager> cmCtor = CameraManager.class.getDeclaredConstructor(Context.class);
        cmCtor.setAccessible(true);
        CameraManager cm = cmCtor.newInstance(new FakeContext(systemContext()));

        System.out.println("cameras: " + Arrays.toString(cm.getCameraIdList()));
        try {
            System.out.println("concurrent camera sets: " + cm.getConcurrentCameraIds());
        } catch (Throwable t) {
            System.out.println("concurrent camera sets: n/a (" + t + ")");
        }
        for (String id : new String[]{ "0", "2", "3" }) {
            CameraCharacteristics c = cm.getCameraCharacteristics(id);
            System.out.println("cam " + id
                    + " focal=" + Arrays.toString(c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS))
                    + " minFocusDpt=" + c.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE)
                    + " hyperfocalDpt=" + c.get(CameraCharacteristics.LENS_INFO_HYPERFOCAL_DISTANCE)
                    + " focusCal=" + c.get(CameraCharacteristics.LENS_INFO_FOCUS_DISTANCE_CALIBRATION)
                    + " pose=" + Arrays.toString(c.get(CameraCharacteristics.LENS_POSE_TRANSLATION))
                    + " intrinsics=" + Arrays.toString(c.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION))
                    + " array=" + c.get(CameraCharacteristics.SENSOR_INFO_PRE_CORRECTION_ACTIVE_ARRAY_SIZE)
                    + " yuv=" + pick(c));
        }

        HandlerThread ht = new HandlerThread("cam");
        ht.start();
        Handler h = new Handler(ht.getLooper());
        Executor ex = h::post;
        Size pairSize = new Size(1280, 960);
        Size macroSize = pick(cm.getCameraCharacteristics("3"));
        ImageReader r0 = reader("main", pairSize, h), r2 = reader("ultrawide", pairSize, h);
        ImageReader r3 = reader("macro", macroSize, h);

        Runnable openPair = () -> {
            try {
                CameraDevice d = open(cm, "4", h);
                OutputConfiguration a = new OutputConfiguration(r0.getSurface());
                a.setPhysicalCameraId("0");
                OutputConfiguration b = new OutputConfiguration(r2.getSurface());
                b.setPhysicalCameraId("2");
                start(d, Arrays.asList(a, b), Arrays.asList(r0, r2), ex, h);
            } catch (Exception e) {
                events.add(now() + " pair: " + e.getMessage());
            }
        };
        Runnable openMacro = () -> {
            try {
                CameraDevice d = open(cm, "3", h);
                start(d, Collections.singletonList(new OutputConfiguration(r3.getSurface())),
                        Collections.singletonList(r3), ex, h);
            } catch (Exception e) {
                events.add(now() + " macro: " + e.getMessage());
            }
        };
        if (order.equals("macro-first")) { openMacro.run(); Thread.sleep(1500); openPair.run(); }
        else { openPair.run(); Thread.sleep(1500); openMacro.run(); }

        long[] before = new long[3];
        Thread.sleep(2000);
        before[0] = counts.get("main")[0]; before[1] = counts.get("ultrawide")[0]; before[2] = counts.get("macro")[0];
        Thread.sleep(4000);
        System.out.println("order: " + order);
        for (String e : new ArrayList<>(events)) System.out.println("  " + e);
        System.out.printf("frames in the last 4 s (all three should be running): main %d, ultrawide %d, macro %d%n",
                counts.get("main")[0] - before[0], counts.get("ultrawide")[0] - before[1], counts.get("macro")[0] - before[2]);
        System.exit(0);
    }
}
