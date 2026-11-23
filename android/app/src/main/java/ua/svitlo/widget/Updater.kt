package ua.svitlo.widget

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.Settings
import java.net.HttpURLConnection
import java.net.URL

/**
 * Self-update from the bot's VPS: <base>/w/app/version.txt holds the latest versionCode,
 * <base>/w/app/svitlo-widget.apk the APK. Android does not allow a silent install outside Google Play,
 * so the app downloads the APK and the system asks the user to confirm.
 */
object Updater {
    private const val DAY_MS = 12 * 3600 * 1000L

    private fun base(link: String) = Uri.parse(link).buildUpon().path("/w/app/").clearQuery().build().toString()

    fun current(context: Context): Long {
        val p = context.packageManager.getPackageInfo(context.packageName, 0)
        return if (Build.VERSION.SDK_INT >= 28) p.longVersionCode else @Suppress("DEPRECATION") p.versionCode.toLong()
    }

    /** Latest versionCode on the server, or 0 when it cannot be read. Call off the main thread. */
    fun latest(link: String): Long = try {
        val c = URL(base(link) + "version.txt").openConnection() as HttpURLConnection
        c.connectTimeout = 10000; c.readTimeout = 10000
        try { if (c.responseCode == 200) c.inputStream.bufferedReader().readText().trim().toLong() else 0 } finally { c.disconnect() }
    } catch (e: Exception) { 0 }

    /** At most twice a day: remember whether a newer version exists, so the widget can say so. */
    fun checkInBackground(context: Context, done: () -> Unit) {
        val prefs = Prefs(context)
        val link = prefs.link
        if (link == null || System.currentTimeMillis() - prefs.updateChecked < DAY_MS) { done(); return }
        Thread {
            val v = latest(link)
            if (v > 0) { prefs.updateChecked = System.currentTimeMillis(); prefs.latestVersion = v }
            done()
        }.start()
    }

    fun available(context: Context) = Prefs(context).latestVersion > current(context)

    /** Download the APK and open the system installer. Returns a message for the user. */
    fun install(context: Context): String {
        val link = Prefs(context).link ?: return "Спершу вставте посилання з бота."
        if (Build.VERSION.SDK_INT >= 26 && !context.packageManager.canRequestPackageInstalls()) {
            context.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + context.packageName))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return "Дозвольте «Світлу» встановлювати застосунки й натисніть «Оновити» ще раз."
        }
        val dm = context.getSystemService(DownloadManager::class.java)
        val id = dm.enqueue(DownloadManager.Request(Uri.parse(base(link) + "svitlo-widget.apk"))
            .setTitle("Світло: оновлення")
            .setDestinationInExternalFilesDir(context, Environment.DIRECTORY_DOWNLOADS, "svitlo-widget-${System.currentTimeMillis()}.apk"))
        val app = context.applicationContext
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                if (i.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1) != id) return
                app.unregisterReceiver(this)
                val uri = dm.getUriForDownloadedFile(id) ?: return
                app.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
            }
        }
        val filter = IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE)
        if (Build.VERSION.SDK_INT >= 33) app.registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
        else app.registerReceiver(receiver, filter)
        return "Завантажую оновлення…"
    }
}
