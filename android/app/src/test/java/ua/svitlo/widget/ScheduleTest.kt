package ua.svitlo.widget

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDateTime

class ScheduleTest {
    private fun at(s: String) = LocalDateTime.parse(s).atZone(Schedule.KYIV)
    private fun day(on: String) = Schedule(at("${on}T00:00").toEpochSecond(), "5.2", "")

    @Test fun slotsAndNextChange() {
        // 00:00-02:00 on, 02:00-04:00 off, rest on.
        val s = day("2026-11-24").copy(s = "1111" + "2222" + "1".repeat(40))
        assertEquals(2, s.slotIndex(at("2026-11-24T01:10")))
        assertEquals(4, s.nextChange(2))
        assertEquals(at("2026-11-24T02:00"), s.slotStart(4))
        assertEquals(8, s.nextChange(4))
        assertEquals(4, s.runLength(4))
        assertEquals(-1, s.slotIndex(at("2026-11-25T00:00")))
    }

    @Test fun dstDaysFollowWallClock() {
        // 2026-03-29: 03:00 jumps to 04:00 in Kyiv; slot 8 (04:00) starts 3 real hours after midnight.
        val spring = day("2026-03-29").copy(s = "1".repeat(48))
        assertEquals(8, spring.slotIndex(at("2026-03-29T04:10")))
        assertEquals(3 * 3600L, spring.slotStart(8).toEpochSecond() - spring.t)
        // 2026-10-25: 04:00 falls back to 03:00; slot 10 (05:00) starts 6 real hours after midnight.
        val fall = day("2026-10-25").copy(s = "1".repeat(48))
        assertEquals(6 * 3600L, fall.slotStart(10).toEpochSecond() - fall.t)
    }

    @Test fun ringFaceAndOverlay() {
        // Today all on; tomorrow off 00:00-01:00. At 12:00 the face shows tomorrow before the hand.
        val s = day("2026-11-24").copy(s = "1".repeat(48) + "22" + "1".repeat(46))
        val m = 12 * 60
        val face = s.faceArcs(m).map { it.toList() }
        assertEquals(listOf(listOf(0, 60, 2), listOf(60, 1440, 1)), face)
        assertEquals(emptyList<List<Int>>(), s.overlayArcs(m).map { it.toList() })
        // Tomorrow off 20:00-21:00 is after the hand: today's state on the face, tomorrow's in the overlay.
        val t = day("2026-11-24").copy(s = "1".repeat(48) + "1".repeat(40) + "22" + "1".repeat(6))
        assertEquals(listOf(listOf(1200, 1260, 2)), t.overlayArcs(m).map { it.toList() })
    }
}
