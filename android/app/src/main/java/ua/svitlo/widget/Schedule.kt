package ua.svitlo.widget

import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit

/**
 * The widget data from the bot (/w/data): t = Kyiv midnight epoch of day 0, q = queue,
 * s = one digit per half hour from day 0 ('0' no data, '1' on, '2' off, '3' possible).
 * Same rules as services/widget.html; slots follow the Kyiv wall clock, so DST days are right.
 */
data class Schedule(val t: Long, val q: String, val s: String) {

    private val day0: LocalDate = Instant.ofEpochSecond(t).atZone(KYIV).toLocalDate()

    /** Wall-clock minutes since day 0 midnight. */
    fun minutes(now: ZonedDateTime): Int {
        val k = now.withZoneSameInstant(KYIV)
        return (ChronoUnit.DAYS.between(day0, k.toLocalDate()) * 1440 + k.hour * 60 + k.minute).toInt()
    }

    fun slotIndex(now: ZonedDateTime): Int { val i = Math.floorDiv(minutes(now), 30); return if (i < 0 || i >= s.length) -1 else i }

    fun stateAt(i: Int): Int = if (i < 0 || i >= s.length) 0 else s[i] - '0'

    /** Same rules as get_next_event_info(): "on" counts only directly after "off" or "possible". */
    fun nextChange(i: Int): Int {
        val c = s.getOrElse(i) { '0' }
        for (k in i + 1 until s.length) {
            val x = s[k]
            if (x != c && x != '0' && !(x == '1' && s[k - 1] != '2' && s[k - 1] != '3')) return k
        }
        return -1
    }

    /** Real time when wall-clock slot j starts. */
    fun slotStart(j: Int): ZonedDateTime = day0.atStartOfDay().plusMinutes(j * 30L).atZone(KYIV)

    fun runLength(j: Int): Int { var k = j; while (k < s.length && s[k] == s[j]) k++; return k - j }

    /** "Коло (Прогноз)": the 0..1440 face; before the hand = tomorrow, after = today. Arcs are [from, to, state]. */
    fun faceArcs(m: Int): List<IntArray> {
        val d = Math.floorDiv(m, 1440); val cm = m - d * 1440
        val a = ArrayList<IntArray>()
        for (k in 0 until 48) {
            val x = k * 30; val y = x + 30; val n = state(d + 1, k); val t = state(d, k)
            if (y <= cm) a.add(intArrayOf(x, y, n)) else if (x >= cm) a.add(intArrayOf(x, y, t))
            else { a.add(intArrayOf(x, cm, n)); a.add(intArrayOf(cm, y, t)) }
        }
        return merge(a)
    }

    /** Outer thin arc after the hand: tomorrow's state where it differs (off/possible, or on where today is off/possible). */
    fun overlayArcs(m: Int): List<IntArray> {
        val d = Math.floorDiv(m, 1440); val cm = m - d * 1440
        val a = ArrayList<IntArray>()
        for (k in 0 until 48) {
            val x = maxOf(k * 30, cm); val y = k * 30 + 30; val n = state(d + 1, k); val t = state(d, k)
            if (y > x && n != t && n != 0 && (n != 1 || t == 2 || t == 3)) a.add(intArrayOf(x, y, n))
        }
        return merge(a)
    }

    private fun state(day: Int, k: Int) = stateAt(day * 48 + k)

    companion object {
        // "Europe/Kiev": the old name exists on every tzdata; "Europe/Kyiv" only since 2022b (old phones crash).
        val KYIV: ZoneId = ZoneId.of("Europe/Kiev")

        fun parse(json: String?): Schedule? = try {
            val o = JSONObject(json ?: "")
            Schedule(o.getLong("t"), o.getString("q"), o.getString("s"))
        } catch (e: Exception) { null }

        private fun merge(a: List<IntArray>): List<IntArray> {
            val r = ArrayList<IntArray>()
            for (x in a) { val p = r.lastOrNull(); if (p != null && p[1] == x[0] && p[2] == x[2]) p[1] = x[1] else r.add(x.copyOf()) }
            return r
        }
    }
}
