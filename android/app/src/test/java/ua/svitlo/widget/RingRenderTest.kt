package ua.svitlo.widget

import android.graphics.Bitmap
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
}
