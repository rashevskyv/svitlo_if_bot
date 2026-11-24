package ua.svitlo.widget

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.Switch
import android.widget.TextView
import com.google.firebase.messaging.FirebaseMessaging

/** One screen: paste the "Живий графік у браузері" link from the bot, press the button, add the widget. */
class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var update: Button
    private lateinit var sched: Switch
    private lateinit var off: Spinner
    private lateinit var on: Spinner

    companion object { private val CHOICES = listOf(0, 5, 10, 15, 30, 60) }

    private fun showUpdate() {
        update.visibility = if (Updater.available(this)) android.view.View.VISIBLE else android.view.View.GONE
        RingWidget.redraw(this)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val input = EditText(this).apply { hint = "http://72.56.126.242:8080/w?r=…&q=…"; setText(intent?.dataString ?: prefs.link ?: "") }
        status = TextView(this)
        val save = Button(this).apply { text = "Зберегти"; setOnClickListener { save(input.text.toString().trim()) } }
        update = Button(this).apply {
            text = "⬆ Оновити застосунок"; visibility = android.view.View.GONE
            setOnClickListener { status.text = Updater.install(this@MainActivity) }
        }
        // Notifications: the bot sends them, so they come even when Telegram notifications are off.
        sched = Switch(this).apply { text = "Сповіщати, коли графік оновився"; isChecked = prefs.notifSched }
        off = minutes(prefs.notifOff); on = minutes(prefs.notifOn)
        setContentView(ScrollView(this).apply { addView(LinearLayout(this@MainActivity).apply {
            orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad)
            addView(TextView(this@MainActivity).apply {
                text = "1. У боті відкрийте графік і скопіюйте посилання «Живий графік у браузері».\n" +
                    "2. Вставте його сюди й натисніть «Зберегти».\n" +
                    "3. Додайте віджет «Світло» на головний екран."
            })
            addView(input)
            addView(TextView(this@MainActivity).apply { text = "\nСповіщення"; textSize = 18f })
            addView(sched)
            addView(TextView(this@MainActivity).apply { text = "Перед вимкненням світла" }); addView(off)
            addView(TextView(this@MainActivity).apply { text = "Перед увімкненням світла" }); addView(on)
            addView(save); addView(status); addView(update)
        }) })
        status.text = if (prefs.data != null) "Збережено. Віджет оновлюється сам." else ""
        prefs.link?.let { link ->
            Thread {
                val v = Updater.latest(link)
                if (v > 0) prefs.latestVersion = v
                runOnUiThread { showUpdate() }
            }.start()
        }
        if (intent?.dataString != null) save(intent.dataString!!)
    }

    private fun minutes(current: Int) = Spinner(this).apply {
        adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, CHOICES.map { if (it == 0) "Не сповіщати" else "За $it хв" })
        setSelection(CHOICES.indexOf(current).coerceAtLeast(0))
    }

    private fun save(link: String) {
        val topic = Prefs.topicFor(link)
        if (topic == null) { status.text = "Це не посилання на графік з бота."; return }
        status.text = "Підключаю…"
        val prefs = Prefs(this)
        prefs.notifSched = sched.isChecked
        prefs.notifOff = CHOICES[off.selectedItemPosition]; prefs.notifOn = CHOICES[on.selectedItemPosition]
        val wantsNotif = prefs.notifSched || prefs.notifOff > 0 || prefs.notifOn > 0
        if (wantsNotif && Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        val old = prefs.topic()
        Thread {
            val msg = try {
                val d = Prefs.subscribe(link)
                if (Schedule.parse(d) == null) throw IllegalStateException("bad data")
                if (old != null && old != topic) FirebaseMessaging.getInstance().unsubscribeFromTopic(old)
                prefs.link = link; prefs.data = d
                RingWidget.redraw(this)
                FirebaseMessaging.getInstance().subscribeToTopic(topic).addOnCompleteListener { t ->
                    status.text = if (t.isSuccessful) "Збережено. Віджет оновлюється сам, коли бот бачить зміну графіка."
                    else "Графік збережено, але push не підключено: ${t.exception?.message}. Спробуйте ще раз."
                }
                // The bot sends notifications to this phone's token; it keeps the settings even when all are off.
                FirebaseMessaging.getInstance().token.addOnCompleteListener { t ->
                    val token = (if (t.isSuccessful) t.result else null) ?: return@addOnCompleteListener
                    Thread {
                        val m = try { prefs.sendNotifPrefs(token); null } catch (e: Exception) { "Сповіщення не збережено: ${e.message}. Спробуйте ще раз." }
                        if (m != null) runOnUiThread { status.text = m }
                    }.start()
                }
                "Збережено. Підключаю push…"
            } catch (e: Exception) { "Помилка: ${e.message}" }
            runOnUiThread { status.text = msg }
        }.start()
    }
}
