package ua.svitlo.widget

import android.graphics.Bitmap
import android.graphics.Canvas
import android.view.View
import android.widget.FrameLayout
import org.robolectric.RuntimeEnvironment
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File
import java.time.LocalDateTime

/** Draws the ring with real Android graphics; the PNGs go to build/ring-*.png to compare with the web page. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34])
class RingRenderTest {
    @Test fun ringIsTransparentWithGaps() {
        val t = LocalDateTime.parse("2026-11-24T00:00").atZone(Schedule.KYIV).toEpochSecond()
        val s = Schedule(t, "5.2", "111111222222211111111111222222211111222221111111" + "111122222221111111111122222221111111222222211111")
        for (night in listOf(false, true)) {
            val b = RingWidget.ring(s, 2 * 60 + 10, night)
            File("build/ring-${if (night) "dark" else "light"}.png").outputStream().use { b.compress(Bitmap.CompressFormat.PNG, 100, it) }
            assertEquals(0, b.getPixel(0, 0) ushr 24)  // corner: transparent
            assertEquals(0, b.getPixel(230, 230 - 75 * 2) ushr 24)  // midnight gap at the top of the face
            assertEquals(0xFF, b.getPixel(410, 242) ushr 24)  // face at 06:15, radius 90: opaque
        }
    }

    /** The whole widget layout (icons, "до" line, duration) to build/widget-<state>.png, to compare with the web page. */
    @Test fun widgetLayout() {
        val ctx = RuntimeEnvironment.getApplication()
        Prefs(ctx).data = """{"t":${LocalDateTime.parse("2026-11-24T00:00").atZone(Schedule.KYIV).toEpochSecond()},"q":"5.2","s":"1111222222221111111111111111333311111111111111111"}"""
        for ((name, hm) in listOf("on" to "01:10", "off" to "02:44")) {
            val (rv, _) = RingWidget.views(ctx, LocalDateTime.parse("2026-11-24T$hm").atZone(Schedule.KYIV))
            val view = rv.apply(ctx, FrameLayout(ctx))
            val w = 720; val h = 480
            view.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY))
            view.layout(0, 0, w, h)
            val b = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
            Canvas(b).apply { drawColor(0xFF5B8DB8.toInt()) }.also { view.draw(it) }
            File("build/widget-$name.png").outputStream().use { b.compress(Bitmap.CompressFormat.PNG, 100, it) }
            assertEquals(View.VISIBLE, view.findViewById<View>(R.id.afterRow).visibility)
        }
    }
}
