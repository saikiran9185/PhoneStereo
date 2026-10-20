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

// Runs via app_process as the shell user (same trick as scrcpy): opens the
// logical multi-camera and asks for both physical streams at once.
public class StereoProbe {
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

    static Size pickSize(CameraCharacteristics a, CameraCharacteristics b) {
        Size[] want = { new Size(1280, 960), new Size(1024, 768), new Size(800, 600), new Size(640, 480) };
        List<Size> sa = Arrays.asList(((StreamConfigurationMap) a.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)).getOutputSizes(ImageFormat.YUV_420_888));
        List<Size> sb = Arrays.asList(((StreamConfigurationMap) b.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)).getOutputSizes(ImageFormat.YUV_420_888));
        for (Size s : want) if (sa.contains(s) && sb.contains(s)) return s;
        return new Size(640, 480);
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

    public static void main(String[] args) throws Exception {
        String logical = args.length > 0 ? args[0] : "4";
        Looper.prepareMainLooper();
        Constructor<CameraManager> cmCtor = CameraManager.class.getDeclaredConstructor(Context.class);
        cmCtor.setAccessible(true);
        CameraManager cm = cmCtor.newInstance(new FakeContext(systemContext()));

        System.out.println("cameras visible: " + Arrays.toString(cm.getCameraIdList()));
        CameraCharacteristics lc = cm.getCameraCharacteristics(logical);
        List<String> phys = new ArrayList<>(lc.getPhysicalCameraIds());
        Collections.sort(phys);
        System.out.println("logical " + logical + " physical ids: " + phys);
        if (phys.size() < 2) { System.out.println("RESULT: not a multi-camera"); System.exit(2); }
        String idA = phys.get(0), idB = phys.get(1);
        CameraCharacteristics ca = cm.getCameraCharacteristics(idA), cb = cm.getCameraCharacteristics(idB);
        for (String id : phys) {
            CameraCharacteristics c = cm.getCameraCharacteristics(id);
            System.out.println("  phys " + id + " focal=" + Arrays.toString(c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS))
                    + " pose=" + Arrays.toString(c.get(CameraCharacteristics.LENS_POSE_TRANSLATION))
                    + " intrinsics=" + Arrays.toString(c.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION)));
        }
        Size size = pickSize(ca, cb);
        System.out.println("stream size: " + size);

        HandlerThread ht = new HandlerThread("cam");
        ht.start();
        Handler handler = new Handler(ht.getLooper());
        Executor exec = handler::post;

        ImageReader ra = ImageReader.newInstance(size.getWidth(), size.getHeight(), ImageFormat.YUV_420_888, 4);
        ImageReader rb = ImageReader.newInstance(size.getWidth(), size.getHeight(), ImageFormat.YUV_420_888, 4);
        final long[] count = new long[2];
        final long[] lastTs = new long[2];
        final long t0 = System.nanoTime();
        final List<Long> skews = Collections.synchronizedList(new ArrayList<>());
        final int[] saved = new int[2];
        ImageReader[] readers = { ra, rb };
        String[] ids = { idA, idB };
        for (int k = 0; k < 2; k++) {
            final int i = k;
            readers[i].setOnImageAvailableListener(r -> {
                Image img = r.acquireLatestImage();
                if (img == null) return;
                try {
                    count[i]++;
                    lastTs[i] = img.getTimestamp();
                    if (lastTs[0] > 0 && lastTs[1] > 0) skews.add(Math.abs(lastTs[0] - lastTs[1]));
                    long ms = (System.nanoTime() - t0) / 1_000_000;
                    if (ms > 2500 && saved[i] < 3 && count[i] % 10 == 0) {
                        saveJpeg(img, OUT + "/cam" + ids[i] + "_" + saved[i] + ".jpg");
                        saved[i]++;
                    }
                } catch (Exception e) {
                    System.out.println("save error: " + e);
                } finally {
                    img.close();
                }
            }, handler);
        }

        CountDownLatch opened = new CountDownLatch(1);
        final CameraDevice[] dev = new CameraDevice[1];
        final String[] err = new String[1];
        cm.openCamera(logical, new CameraDevice.StateCallback() {
            @Override public void onOpened(CameraDevice d) { dev[0] = d; opened.countDown(); }
            @Override public void onDisconnected(CameraDevice d) { err[0] = "disconnected"; d.close(); opened.countDown(); }
            @Override public void onError(CameraDevice d, int e) { err[0] = "open error " + e; d.close(); opened.countDown(); }
        }, handler);
        opened.await(10, TimeUnit.SECONDS);
        if (dev[0] == null) { System.out.println("RESULT: could not open logical camera: " + err[0]); System.exit(3); }
        System.out.println("opened logical camera " + logical);

        OutputConfiguration oa = new OutputConfiguration(ra.getSurface());
        oa.setPhysicalCameraId(idA);
        OutputConfiguration ob = new OutputConfiguration(rb.getSurface());
        ob.setPhysicalCameraId(idB);

        CountDownLatch configured = new CountDownLatch(1);
        final CameraCaptureSession[] sess = new CameraCaptureSession[1];
        SessionConfiguration sc = new SessionConfiguration(SessionConfiguration.SESSION_REGULAR,
                Arrays.asList(oa, ob), exec, new CameraCaptureSession.StateCallback() {
                    @Override public void onConfigured(CameraCaptureSession s) { sess[0] = s; configured.countDown(); }
                    @Override public void onConfigureFailed(CameraCaptureSession s) { err[0] = "configure failed"; configured.countDown(); }
                });
        try {
            System.out.println("isSessionConfigurationSupported: " + dev[0].isSessionConfigurationSupported(sc));
        } catch (Exception e) {
            System.out.println("isSessionConfigurationSupported threw: " + e);
        }
        dev[0].createCaptureSession(sc);
        configured.await(10, TimeUnit.SECONDS);
        if (sess[0] == null) { System.out.println("RESULT: session failed: " + err[0]); dev[0].close(); System.exit(4); }

        CaptureRequest.Builder rq = dev[0].createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
        rq.addTarget(ra.getSurface());
        rq.addTarget(rb.getSurface());
        sess[0].setRepeatingRequest(rq.build(), null, handler);

        Thread.sleep(6000);
        sess[0].stopRepeating();
        sess[0].close();
        dev[0].close();

        double secs = (System.nanoTime() - t0) / 1e9;
        System.out.printf("frames cam%s=%d (%.1f fps)  cam%s=%d (%.1f fps)%n",
                idA, count[0], count[0] / secs, idB, count[1], count[1] / secs);
        List<Long> sk;
        synchronized (skews) { sk = new ArrayList<>(skews); }
        if (!sk.isEmpty()) {
            Collections.sort(sk);
            System.out.printf("timestamp gap between streams: median %.2f ms, p90 %.2f ms%n",
                    sk.get(sk.size() / 2) / 1e6, sk.get((int) (sk.size() * 0.9)) / 1e6);
        }
        System.out.println(count[0] > 10 && count[1] > 10 ? "RESULT: BOTH STREAMS RUNNING" : "RESULT: only one or no stream delivered frames");
        System.exit(0);
    }
}
