package ua.svitlo.widget

import android.app.AlarmManager
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RectF
import android.graphics.Typeface
import android.os.SystemClock
import android.view.View
import android.widget.RemoteViews
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.cos
import kotlin.math.sin

/**
 * Home-screen widget, same design as services/widget.html (check changes there in a browser first).
 * No network here: the data comes by FCM push (PushService) or from MainActivity. The ring is redrawn
 * locally every 10 minutes and at each state change; the countdown is a Chronometer that ticks by itself.
 */
class RingWidget : AppWidgetProvider() {

    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        redraw(context)
        val pending = goAsync()
        Updater.checkInBackground(context) { redraw(context); pending.finish() }
    }

    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        if (intent.action == ACTION_REDRAW) redraw(context)
    }

    companion object {
        private const val ACTION_REDRAW = "ua.svitlo.widget.REDRAW"
        private val NAMES = arrayOf("Немає даних", "Світло є", "Світла немає", "Можливе відключення")
        private val COL = intArrayOf(0xFF9E9E9E.toInt(), 0xFF4CAF50.toInt(), 0xFFD32F2F.toInt(), 0xFF81D4FA.toInt())
        private const val BLUE = 0xFF2196F3.toInt()
        private val HM = DateTimeFormatter.ofPattern("HH:mm")

        fun redraw(context: Context) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, RingWidget::class.java))
            if (ids.isEmpty()) return
            val data = Schedule.parse(Prefs(context).data)
            val now = ZonedDateTime.now(Schedule.KYIV)
            val v = RemoteViews(context.packageName, R.layout.widget)
            v.setOnClickPendingIntent(R.id.root, PendingIntent.getActivity(context, 0,
                Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE))
            var nextRedraw = now.plusMinutes(10)
            v.setViewVisibility(R.id.countdown, View.GONE)
            v.setTextViewText(R.id.next, ""); v.setTextViewText(R.id.after, "")
            if (data == null) {
                v.setTextViewText(R.id.status, NAMES[0])
                v.setTextViewText(R.id.next, "Торкніться і вставте посилання з бота")
            } else {
                val m = data.minutes(now); val i = data.slotIndex(now); val c = data.stateAt(i)
                val j = if (i < 0) -1 else data.nextChange(i)
                val night = context.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
                v.setImageViewBitmap(R.id.ring, ring(data, m, night))
                v.setTextViewText(R.id.status, NAMES[c])
                v.setInt(R.id.bar, "setBackgroundColor", COL[c])
                v.setViewVisibility(R.id.old, if (i < 0) View.VISIBLE else View.GONE)
                if (j < 0) {
                    if (i >= 0) v.setTextViewText(R.id.next, "Змін не заплановано")
                } else {
                    val at = data.slotStart(j)
                    val day = if (at.toLocalDate() != now.toLocalDate()) " завтра" else ""
                    v.setTextViewText(R.id.next, "до ${at.format(HM)}$day")
                    val left = at.toInstant().toEpochMilli() - System.currentTimeMillis()
                    v.setChronometer(R.id.countdown, SystemClock.elapsedRealtime() + left, null, true)
                    v.setChronometerCountDown(R.id.countdown, true)
                    v.setViewVisibility(R.id.countdown, View.VISIBLE)
                    v.setTextViewText(R.id.after, "Далі: ${NAMES[data.stateAt(j)]}, ${data.runLength(j) / 2.0} год".replace(".0 ", " "))
                    if (at.isBefore(nextRedraw)) nextRedraw = at.plusSeconds(1)
                }
            }
            if (Updater.available(context)) v.setTextViewText(R.id.after, "⬆ Є оновлення — торкніться")
            manager.updateAppWidget(ids, v)
            // Inexact alarm: no exact-alarm permission needed; a few minutes late is fine for the hand.
            // ponytail: a light/dark switch shows in the ring centre at the next redraw (≤10 min), not at once.
            val am = context.getSystemService(AlarmManager::class.java)
            am.set(AlarmManager.RTC, nextRedraw.toInstant().toEpochMilli(), PendingIntent.getBroadcast(context, 1,
                Intent(context, RingWidget::class.java).setAction(ACTION_REDRAW), PendingIntent.FLAG_IMMUTABLE))
        }

        /** The ring of services/widget.html on a transparent bitmap; 1.0 of the bot image = 100 units. */
        fun ring(d: Schedule, m: Int, night: Boolean, px: Int = 460): Bitmap {
            val b = Bitmap.createBitmap(px, px, Bitmap.Config.ARGB_8888)
            val c = Canvas(b)
            c.translate(px / 2f, px / 2f); c.scale(px / 230f, px / 230f)
            val p = Paint(Paint.ANTI_ALIAS_FLAG)
            fun arc(x: Int, y: Int, r1: Float, r2: Float, color: Int) {
                val r = (r1 + r2) / 2
                p.style = Paint.Style.STROKE; p.strokeWidth = r2 - r1; p.strokeCap = Paint.Cap.BUTT; p.color = color
                c.drawArc(RectF(-r, -r, r, r), x / 4f - 90, (y - x) / 4f, false, p)
            }
            fun pt(x: Float, r: Float): Pair<Float, Float> { val a = x / 720.0 * Math.PI; return Pair((sin(a) * r).toFloat(), (-cos(a) * r).toFloat()) }
            fun line(x: Float, r1: Float, r2: Float, w: Float) {
                val (x1, y1) = pt(x, r1); val (x2, y2) = pt(x, r2)
                p.style = Paint.Style.STROKE; p.strokeWidth = w; p.strokeCap = Paint.Cap.ROUND
                c.drawLine(x1, y1, x2, y2, p)
            }
            for (a in d.faceArcs(m)) arc(a[0], a[1], 60f, 100f, COL[a[2]])
            for (a in d.overlayArcs(m)) arc(a[0], a[1], 102f, 108f, COL[a[2]])
            val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { textAlign = Paint.Align.CENTER; typeface = Typeface.DEFAULT_BOLD }
            text.color = Color.WHITE; text.textSize = 10f
            for (i in 0 until 24) { val (x, y) = pt(i * 60f + 30, 80f); c.drawText("%02d".format(i), x, y + 4f, text) }
            // Hour dividers and the midnight bar are gaps: cut them out so the home screen shows through.
            p.xfermode = PorterDuffXfermode(PorterDuff.Mode.CLEAR)
            for (i in 0 until 24) line(i * 60f, 60f, 100f, .8f)
            line(0f, 55f, 105f, 4f)
            p.xfermode = null
            val cm = Math.floorMod(m, 1440).toFloat()
            p.color = BLUE; line(cm, 55f, 105f, 4f)
            val (ex, ey) = pt(cm, 105f)
            p.style = Paint.Style.FILL; c.drawCircle(ex, ey, 4.5f, p)
            p.style = Paint.Style.STROKE; p.strokeWidth = 2f; p.color = Color.WHITE; c.drawCircle(ex, ey, 4.5f, p)
            val fg = if (night) 0xFFEEEEEE.toInt() else 0xFF222222.toInt()
            val muted = if (night) 0xFF999999.toInt() else 0xFF666666.toInt()
            text.color = fg; text.textSize = 18f; c.drawText(d.q, 0f, -8f, text)
            text.color = muted; text.textSize = 11f; c.drawText("Прогноз (24 год)", 0f, 12f, text)
            val day = java.time.Instant.ofEpochSecond(d.t).atZone(Schedule.KYIV).toLocalDate().plusDays(Math.floorDiv(m, 1440).toLong())
            text.textSize = 8f; text.typeface = Typeface.DEFAULT
            c.drawText(day.format(DateTimeFormatter.ofPattern("dd.MM.yyyy")), 0f, 26f, text)
            return b
        }
    }
}
