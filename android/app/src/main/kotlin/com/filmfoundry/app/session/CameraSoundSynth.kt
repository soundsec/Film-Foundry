package com.filmfoundry.app.session

/** Short mechanical cues synthesized in the app. They are not recordings of a camera. */
object CameraSoundSynth {
    const val RATE = 22050
    enum class Cue { SHUTTER, MIRROR, ADVANCE, MOTOR, REWIND }

    /** Faster than this, the two curtains overlap into one click. 1/25 s. */
    const val SHUTTER_SYNC_NS = 40_000_000L
    /** A longer exposure still closes, but the scheduled click does not wait past half a minute. */
    const val SHUTTER_GAP_CAP_MS = 30_000L

    fun samples(cue: Cue): ShortArray = when (cue) {
        Cue.SHUTTER -> burst(0.08, clicks = intArrayOf(0, 38), decayMs = 18, amp = 0.85)
        Cue.MIRROR -> burst(0.16, clicks = intArrayOf(0), decayMs = 70, amp = 0.7, tone = 90.0)
        Cue.ADVANCE -> burst(0.48, clicks = intArrayOf(0, 70, 150, 240, 340), decayMs = 28, amp = 0.55)
        Cue.MOTOR -> burst(0.36, clicks = intArrayOf(), decayMs = 0, amp = 0.28, tone = 140.0, whir = true)
        Cue.REWIND -> burst(1.6, clicks = intArrayOf(), decayMs = 0, amp = 0.3, tone = 78.0, whir = true)
    }

    /** Milliseconds from the opening curtain to the closing curtain. Zero means one overlapped click. */
    fun shutterGapMs(exposureNs: Long?): Long {
        if (exposureNs == null || exposureNs <= SHUTTER_SYNC_NS) return 0L
        return (exposureNs / 1_000_000L).coerceAtMost(SHUTTER_GAP_CAP_MS)
    }

    /** One curtain. A slow shutter plays this twice, separated by [shutterGapMs]. */
    fun curtain(): ShortArray = burst(0.045, clicks = intArrayOf(0), decayMs = 16, amp = 0.9)

    private fun burst(seconds: Double, clicks: IntArray, decayMs: Int, amp: Double, tone: Double = 0.0, whir: Boolean = false): ShortArray {
        val count = (RATE * seconds).toInt()
        val out = ShortArray(count)
        var noise = 0x1234
        for (i in 0 until count) {
            noise = noise * 1103515245 + 12345
            val white = ((noise ushr 16) and 0xffff) / 32768.0 - 1.0
            var sample = 0.0
            if (whir) {
                val hum = kotlin.math.sin(2.0 * Math.PI * tone * i / RATE)
                val slow = 0.65 + 0.35 * kotlin.math.sin(2.0 * Math.PI * 18.0 * i / RATE)
                sample = white * 0.35 * slow + hum * 0.2
            }
            if (tone > 0 && !whir) sample += kotlin.math.sin(2.0 * Math.PI * tone * i / RATE) * kotlin.math.exp(-i / (RATE * 0.05))
            out[i] = (sample * amp * 32767).toInt().coerceIn(-32767, 32767).toShort()
        }
        val decay = (RATE * decayMs / 1000).coerceAtLeast(1)
        for (atMs in clicks) {
            val at = RATE * atMs / 1000
            for (i in 0 until decay) {
                val n = at + i
                if (n >= count) break
                noise = noise * 1103515245 + 12345
                val white = ((noise ushr 16) and 0xffff) / 32768.0 - 1.0
                val env = kotlin.math.exp(-3.0 * i / decay)
                val mixed = out[n] / 32767.0 + white * amp * env
                out[n] = (mixed * 32767).toInt().coerceIn(-32767, 32767).toShort()
            }
        }
        return out
    }
}
