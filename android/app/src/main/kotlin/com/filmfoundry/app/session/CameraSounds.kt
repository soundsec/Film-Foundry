package com.filmfoundry.app.session

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.SoundPool
import android.net.Uri
import android.os.Handler
import android.os.Looper

/** Plays the camera cues. A saved file replaces that cue; a missing file falls back to the built-in sound. */
class CameraSounds private constructor(context: Context) {
    private val app = context.applicationContext
    private val prefs = app.getSharedPreferences("camera_sounds", Context.MODE_PRIVATE)
    private val main = Handler(Looper.getMainLooper())
    private val builtIn = CameraSoundSynth.Cue.entries.associateWith { pcm(CameraSoundSynth.samples(it)) }
    private val pool = SoundPool.Builder().setMaxStreams(5).setAudioAttributes(attributes()).build()
    private val loaded = mutableMapOf<CameraSoundSynth.Cue, Int>()
    private val streams = mutableMapOf<Int, Int>()
    private var motor: AudioTrack? = null
    private var motorStream = 0
    private val pending = mutableListOf<Runnable>()

    init {
        pool.setOnLoadCompleteListener { _, sampleId, status ->
            if (status == 0) CameraSoundSynth.Cue.entries.firstOrNull { streams[sampleId] == it.ordinal }?.let { loaded[it] = sampleId }
        }
        CameraSoundSynth.Cue.entries.forEach { preload(it) }
    }

    fun play(cue: CameraSoundSynth.Cue, delayMs: Long = 0) = later(delayMs) { start(cue, loop = false) }

    /** Fast or unknown speeds play one click. Slower speeds play the opening curtain, then the closing curtain. */
    fun playShutter(exposureNs: Long?, delayMs: Long = 0) {
        val gap = CameraSoundSynth.shutterGapMs(exposureNs)
        later(delayMs) {
            if (gap == 0L || isCustom(CameraSoundSynth.Cue.SHUTTER)) start(CameraSoundSynth.Cue.SHUTTER, loop = false)
            else startPcm(CameraSoundSynth.curtain(), loop = false)
            if (gap > 0L) later(gap) {
                if (isCustom(CameraSoundSynth.Cue.SHUTTER)) start(CameraSoundSynth.Cue.SHUTTER, loop = false)
                else startPcm(CameraSoundSynth.curtain(), loop = false)
            }
        }
    }

    fun cancelPending() {
        pending.toList().forEach { main.removeCallbacks(it) }
        pending.clear()
    }

    fun startMotor() = main.post { start(CameraSoundSynth.Cue.MOTOR, loop = true) }
    fun stopMotor() = main.post {
        if (motorStream != 0) pool.stop(motorStream)
        motorStream = 0
        val track = motor
        motor = null
        // Looping tracks have no completion marker, so stopping owns their release.
        if (track != null) try {
            track.stop()
        } finally {
            track.release()
        }
    }

    fun use(cue: CameraSoundSynth.Cue, uri: Uri) {
        prefs.edit().putString(cue.name, uri.toString()).apply()
        forget(cue)
        preload(cue)
    }

    fun clear(cue: CameraSoundSynth.Cue) {
        prefs.edit().remove(cue.name).apply()
        forget(cue)
    }

    private fun forget(cue: CameraSoundSynth.Cue) {
        loaded.remove(cue)?.let { pool.unload(it) }
        streams.entries.removeAll { it.value == cue.ordinal }
    }

    fun isCustom(cue: CameraSoundSynth.Cue) = prefs.getString(cue.name, null) != null

    private fun preload(cue: CameraSoundSynth.Cue) {
        val uri = prefs.getString(cue.name, null)?.let(Uri::parse) ?: return
        val descriptor = runCatching { app.contentResolver.openAssetFileDescriptor(uri, "r") }.getOrNull() ?: return
        descriptor.use { pool.load(it, 1).also { id -> streams[id] = cue.ordinal } }
    }

    private fun later(delayMs: Long, action: () -> Unit) {
        lateinit var task: Runnable
        task = Runnable {
            pending.remove(task)
            action()
        }
        pending.add(task)
        main.postDelayed(task, delayMs.coerceAtLeast(0L))
    }

    private fun startPcm(samples: ShortArray, loop: Boolean) {
        val track = track(pcm(samples), loop)
        if (loop) motor = track
        track.play()
    }

    private fun start(cue: CameraSoundSynth.Cue, loop: Boolean) {
        if (cue == CameraSoundSynth.Cue.MOTOR && (motor != null || motorStream != 0)) return
        val sample = loaded[cue]
        if (sample != null) {
            val stream = pool.play(sample, 1f, 1f, 1, if (loop) -1 else 0, 1f)
            if (loop) motorStream = stream
            return
        }
        val track = track(builtIn.getValue(cue), loop)
        if (loop) motor = track
        track.play()
    }

    private fun track(pcm: ByteArray, loop: Boolean): AudioTrack {
        val track = AudioTrack.Builder()
            .setAudioAttributes(attributes())
            .setAudioFormat(AudioFormat.Builder().setSampleRate(CameraSoundSynth.RATE).setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setBufferSizeInBytes(pcm.size)
            .build()
        track.write(pcm, 0, pcm.size)
        if (loop) track.setLoopPoints(0, pcm.size / 2, -1)
        else track.setNotificationMarkerPosition(pcm.size / 2).also {
            track.setPlaybackPositionUpdateListener(object : AudioTrack.OnPlaybackPositionUpdateListener {
                override fun onMarkerReached(track: AudioTrack) { track.release() }
                override fun onPeriodicNotification(track: AudioTrack) = Unit
            })
        }
        return track
    }

    private fun attributes() = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()

    companion object {
        @Volatile private var instance: CameraSounds? = null
        fun get(context: Context): CameraSounds = instance ?: synchronized(this) {
            instance ?: CameraSounds(context.applicationContext).also { instance = it }
        }
    }

    private fun pcm(samples: ShortArray): ByteArray {
        val bytes = ByteArray(samples.size * 2)
        for (i in samples.indices) {
            bytes[i * 2] = (samples[i].toInt() and 0xff).toByte()
            bytes[i * 2 + 1] = (samples[i].toInt() shr 8).toByte()
        }
        return bytes
    }
}
