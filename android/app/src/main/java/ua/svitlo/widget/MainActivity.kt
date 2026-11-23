package ua.svitlo.widget

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import com.google.firebase.messaging.FirebaseMessaging

/** One screen: paste the "Живий графік у браузері" link from the bot, press the button, add the widget. */
class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var update: Button

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
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad)
            addView(TextView(this@MainActivity).apply {
                text = "1. У боті відкрийте графік і скопіюйте посилання «Живий графік у браузері».\n" +
                    "2. Вставте його сюди й натисніть «Зберегти».\n" +
                    "3. Додайте віджет «Світло» на головний екран."
            })
            addView(input); addView(save); addView(status); addView(update)
        })
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

    private fun save(link: String) {
        val topic = Prefs.topicFor(link)
        if (topic == null) { status.text = "Це не посилання на графік з бота."; return }
        status.text = "Підключаю…"
        val prefs = Prefs(this)
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
                "Збережено. Підключаю push…"
            } catch (e: Exception) { "Помилка: ${e.message}" }
            runOnUiThread { status.text = msg }
        }.start()
    }
}
