package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.os.*
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.lifecycle.*
import com.filmfoundry.platform.GyroscopeAgitationRecorder
import java.util.Locale

/** Samples only an explicit rehearsal. Applying freezes ordinary bath numbers, never sensor objects. */
fun recordBathAgitation(activity: ComponentActivity,shell: MobileShell,apply: (Double,Double)->Unit) {
    fun c(zh: String,en: String)=shell.copy(zh,en)
    val recorder=GyroscopeAgitationRecorder(activity)
    val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(12),shell.dp(20),shell.dp(12)) }
    shell.note(panel,"记录旋转／倒置摇晃，最多60秒。应用后填写为每浴累计摇晃秒数和强度，可继续手动修改；不是实时运行8分钟冲洗，也不会在后台监听。","Record rotational/inversion agitation for up to 60 seconds. Apply total active seconds and intensity to the bath, then edit if needed. This does not run an eight-minute bath or listen in the background.")
    val enabled=CheckBox(activity).apply { text=c("启用本次陀螺仪记录","Enable gyroscope for this recording");isEnabled=recorder.available };panel.addView(enabled)
    val status=TextView(activity);panel.addView(status)
    val start=Button(activity).apply { text=c("开始记录","Start recording");isEnabled=false };panel.addView(start)
    val stop=Button(activity).apply { text=c("停止","Stop");isEnabled=false };panel.addView(stop)
    var finished=false;var startedAt=0L
    val handler=Handler(Looper.getMainLooper())
    val dialog=AlertDialog.Builder(activity).setTitle(c("手机摇晃记录","Phone agitation recording")).setView(panel)
        .setNegativeButton(c("取消","Cancel"),null).setPositiveButton(c("使用这次摇晃","Use this recording"),null).create()
    fun update() {
        val sample=recorder.summary()
        status.text=if(!recorder.available) c("此设备没有陀螺仪，保留手动摇晃设置。","No gyroscope is available. Keep manual agitation settings.") else
            String.format(Locale.ROOT,c("记录 %.1f 秒 · 摇晃 %.1f 秒 · 强度 %.2f","Recorded %.1f s · active %.1f s · intensity %.2f"),sample.elapsedSeconds,sample.activeSeconds,sample.strength)
        dialog.getButton(AlertDialog.BUTTON_POSITIVE)?.isEnabled=finished && sample.samples>=2
    }
    fun finish() { recorder.close();finished=true;stop.isEnabled=false;start.isEnabled=enabled.isChecked;update() }
    val tick=object: Runnable { override fun run() {
        if(!recorder.recording) return
        if(SystemClock.elapsedRealtime()-startedAt>=60_000) finish() else { update();handler.postDelayed(this,200) }
    } }
    enabled.setOnCheckedChangeListener { _,checked ->
        if(!checked) { recorder.close();handler.removeCallbacks(tick);finished=false;stop.isEnabled=false }
        start.isEnabled=checked && recorder.available && !recorder.recording;update()
    }
    start.setOnClickListener { finished=false;if(recorder.start()) { startedAt=SystemClock.elapsedRealtime();start.isEnabled=false;stop.isEnabled=true;handler.post(tick) } else { status.text=c("注册失败，继续用手动设置。","Sensor registration failed; use manual controls.") } }
    stop.setOnClickListener { handler.removeCallbacks(tick);finish() }
    val observer=LifecycleEventObserver { _,event -> if(event==Lifecycle.Event.ON_STOP) dialog.dismiss() }
    dialog.setOnDismissListener { recorder.close();handler.removeCallbacksAndMessages(null);activity.lifecycle.removeObserver(observer) }
    dialog.setOnShowListener {
        update();dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val result=recorder.summary();if(finished && enabled.isChecked && result.samples>=2) { apply(result.activeSeconds,result.strength);dialog.dismiss() }
        }
    }
    activity.lifecycle.addObserver(observer);shell.decorate(panel);dialog.show()
}
