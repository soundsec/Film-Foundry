package com.filmfoundry.app.session

import android.app.*
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.*
import java.util.concurrent.Executors

/** Silent foreground processing only: no camera, sensors, or hidden automatic task restart. */
class ProcessingService: Service() {
    private val executor=Executors.newSingleThreadExecutor { runnable -> Thread({ android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND);runnable.run() },"FilmFoundry-processing") }
    private val handler=Handler(Looper.getMainLooper())
    private var active: ProcessingWork.Work?=null
    private var wake: PowerManager.WakeLock?=null
    private var renewal=0L
    private var lastText=""
    private val notifications get()=getSystemService(NotificationManager::class.java)
    private fun copy(zh: String,en: String)=if(resources.configuration.locales[0].language=="zh") zh else en
    private fun notification(job: ProcessingWork.Work): Notification {
        val open=PendingIntent.getActivity(this,0,Intent(this,com.filmfoundry.app.ProcessHostActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val pause=PendingIntent.getService(this,1,Intent(this,ProcessingService::class.java).setAction("pause").putExtra("job_id",job.id),PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_menu_manage)
            .setContentTitle(job.title).setContentText(job.progress).setContentIntent(open)
            .setOnlyAlertOnce(true).setOngoing(true).setProgress(0,0,true).setCategory(Notification.CATEGORY_PROGRESS)
            .setVisibility(Notification.VISIBILITY_PRIVATE).setShowWhen(false)
            .addAction(Notification.Action.Builder(null,copy("暂停","Pause"),pause).build()).build()
    }
    private val update=object: Runnable {
        override fun run() {
            val job=active ?: return
            if(SystemClock.elapsedRealtime()>=renewal) holdCpu()
            if(lastText!=job.progress) { lastText=job.progress;notifications.notify(NOTIFICATION,notification(job)) }
            handler.postDelayed(this,1000)
        }
    }
    private fun holdCpu() {
        val lock=wake ?: (getSystemService(POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"FilmFoundry:processing").apply { setReferenceCounted(false) }.also { wake=it }
        lock.acquire(10*60*1000L);renewal=SystemClock.elapsedRealtime()+5*60*1000L
    }
    override fun onCreate() {
        super.onCreate()
        notifications.createNotificationChannel(NotificationChannel(CHANNEL,copy("处理任务","Processing tasks"),NotificationManager.IMPORTANCE_LOW).apply {
            description=copy("冲洗、扫描与导出可在锁屏后继续；通知中可暂停。","Development, scans and exports continue with the screen off; pause from the notification.")
            setSound(null,null);enableVibration(false);setShowBadge(false)
        })
    }
    override fun onStartCommand(intent: Intent?,flags: Int,startId: Int): Int {
        val id=intent?.getLongExtra("job_id",-1) ?: -1
        if(intent?.action=="pause") { ProcessingWork.pause(id);if(active==null) stopSelf(startId);return START_NOT_STICKY }
        val job=ProcessingWork.claim(id) ?: run { if(active==null) stopSelf(startId);return START_NOT_STICKY }
        active=job
        try {
            if(Build.VERSION.SDK_INT>=29) startForeground(NOTIFICATION,notification(job),if(Build.VERSION.SDK_INT>=35) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING else ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            else startForeground(NOTIFICATION,notification(job))
            holdCpu();handler.post(update)
        } catch(error: Exception) {
            release();active=null;stopSelf(startId);ProcessingWork.finish(job,Result.failure(error));return START_NOT_STICKY
        }
        executor.execute {
            val result=try { job.body() } catch(error: Throwable) { Result.failure(error) }
            handler.post {
                release();active=null;stopSelf(startId)
                ProcessingWork.finish(job,result)
            }
        }
        return START_NOT_STICKY
    }
    override fun onTimeout(startId: Int,fgsType: Int) {
        active?.let { ProcessingWork.pause(it.id) };release();stopSelf()
    }
    private fun release() {
        handler.removeCallbacks(update)
        wake?.let { if(it.isHeld) it.release() };wake=null
        stopForeground(STOP_FOREGROUND_REMOVE)
    }
    override fun onDestroy() {
        active?.let { ProcessingWork.pause(it.id) };release();executor.shutdown();super.onDestroy()
    }
    override fun onBind(intent: Intent?)=null
    companion object { private const val CHANNEL="film_processing_v1";private const val NOTIFICATION=4101 }
}
