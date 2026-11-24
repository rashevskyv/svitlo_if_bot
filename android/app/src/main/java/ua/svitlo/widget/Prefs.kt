package ua.svitlo.widget

import android.content.Context
import android.net.Uri
import java.net.HttpURLConnection
import java.net.URL

/** One queue for all widgets on the phone. ponytail: per-widget queues if someone needs two queues. */
class Prefs(context: Context) {
    private val p = context.getSharedPreferences("svitlo", Context.MODE_PRIVATE)
    var link: String? get() = p.getString("link", null); set(v) = p.edit().putString("link", v).apply()
    var data: String? get() = p.getString("data", null); set(v) = p.edit().putString("data", v).apply()
    var updateChecked: Long get() = p.getLong("updateChecked", 0); set(v) = p.edit().putLong("updateChecked", v).apply()
    var latestVersion: Long get() = p.getLong("latestVersion", 0); set(v) = p.edit().putLong("latestVersion", v).apply()
    // Notifications (sent by the bot): schedule update, and minutes before power off / on (0 = off).
    var notifSched: Boolean get() = p.getBoolean("notifSched", false); set(v) = p.edit().putBoolean("notifSched", v).apply()
    var notifOff: Int get() = p.getInt("notifOff", 0); set(v) = p.edit().putInt("notifOff", v).apply()
    var notifOn: Int get() = p.getInt("notifOn", 0); set(v) = p.edit().putInt("notifOn", v).apply()

    /** GET <base>/w/prefs: the bot stores this phone's notification settings. Call off the main thread. */
    fun sendNotifPrefs(token: String) {
        val u = Uri.parse(link ?: return)
        val url = u.buildUpon().path("/w/prefs").appendQueryParameter("token", token)
            .appendQueryParameter("sched", if (notifSched) "1" else "0")
            .appendQueryParameter("off", notifOff.toString()).appendQueryParameter("on", notifOn.toString()).build().toString()
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 15000; c.readTimeout = 15000
        try { if (c.responseCode != 200) throw IllegalStateException("HTTP ${c.responseCode}") } finally { c.disconnect() }
    }

    /** FCM topic for the queue in [link], e.g. q_ivano-frankivska-oblast_5.2 (the bot sends to the same name). */
    fun topic(): String? = link?.let { topicFor(it) }

    companion object {
        fun topicFor(link: String): String? {
            val u = Uri.parse(link)
            val r = u.getQueryParameter("r") ?: return null
            val q = u.getQueryParameter("q") ?: return null
            return "q_${r}_$q".replace(Regex("[^A-Za-z0-9\\-_.~%]"), "_")
        }

        /** GET <base>/w/sub?r=&q=: the bot starts pushing this queue and returns its current data. */
        fun subscribe(link: String): String {
            val u = Uri.parse(link)
            val url = u.buildUpon().path("/w/sub").build().toString()
            val c = URL(url).openConnection() as HttpURLConnection
            c.connectTimeout = 15000; c.readTimeout = 15000
            try {
                if (c.responseCode != 200) throw IllegalStateException("HTTP ${c.responseCode}")
                return c.inputStream.bufferedReader().readText()
            } finally { c.disconnect() }
        }
    }
}
