package ua.svitlo.widget

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/** The bot sends {"d": <widget data JSON>} to the queue topic when the schedule changes (and at midnight). */
class PushService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        val d = message.data["d"] ?: return
        val prefs = Prefs(this)
        val s = Schedule.parse(d) ?: return
        val mine = prefs.link?.let { android.net.Uri.parse(it).getQueryParameter("q") }
        if (s.q != mine) return  // a topic left over from an earlier queue
        prefs.data = d
        RingWidget.redraw(this)
    }
}
