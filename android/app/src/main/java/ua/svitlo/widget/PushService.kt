package ua.svitlo.widget

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.app.Notification
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * Two kinds of data messages from the bot:
 * {"d": <widget data JSON>} to the queue topic when the schedule changes (and at midnight): redraw the widget;
 * {"title", "body"} to this phone's token: a notification the user asked for in MainActivity.
 */
class PushService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        message.data["title"]?.let { show(this, it, message.data["body"] ?: ""); return }
        val d = message.data["d"] ?: return
        val prefs = Prefs(this)
        val s = Schedule.parse(d) ?: return
        val mine = prefs.link?.let { android.net.Uri.parse(it).getQueryParameter("q") }
        if (s.q != mine) return  // a topic left over from an earlier queue
        prefs.data = d
        RingWidget.redraw(this)
    }

    /** A new token: the bot must know it, or the notifications stop. */
    override fun onNewToken(token: String) {
        try { Prefs(this).sendNotifPrefs(token) } catch (_: Exception) { }  // ponytail: retried at the next "Зберегти"
    }

    companion object {
        private const val CHANNEL = "svitlo"

        fun show(context: Context, title: String, body: String) {
            val nm = context.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Світло", NotificationManager.IMPORTANCE_HIGH))
            val open = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
            val n = Notification.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.bulb_on).setContentTitle(title).setContentText(body)
                .setContentIntent(open).setAutoCancel(true).build()
            nm.notify((System.currentTimeMillis() / 1000).toInt(), n)
        }
    }
}
