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
