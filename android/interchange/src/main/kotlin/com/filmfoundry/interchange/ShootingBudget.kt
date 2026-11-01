package com.filmfoundry.interchange

/** Capture size is film precision. Motion mode picks the largest listed size under the pace cap. */
object ShootingBudget {
    const val STILL_TARGET_FPS = 5
    const val MOTION_TARGET_FPS = 36
    const val MOTION_MAX_LONG_EDGE = 640
    const val MOTION_MAX_PIXELS = 640 * 360

    fun motion(sizes: List<Pair<Int, Int>>): Pair<Int, Int> {
        val legal = sizes.filter { it.first > 0 && it.second > 0 }
        val landscape = legal.filter { it.first >= it.second }.ifEmpty { legal }
        val capped = landscape.filter { maxOf(it.first, it.second) <= MOTION_MAX_LONG_EDGE && it.first.toLong() * it.second <= MOTION_MAX_PIXELS }
        if (capped.isNotEmpty()) return capped.maxBy { it.first.toLong() * it.second }
        return landscape.minByOrNull { it.first.toLong() * it.second } ?: (MOTION_MAX_LONG_EDGE to MOTION_MAX_PIXELS / MOTION_MAX_LONG_EDGE)
    }

    /** An advertised range. Exact 36/36 wins; otherwise the tightest range that contains 36. Never invents a range the device did not list. */
    fun motionFps(ranges: List<Pair<Int, Int>>): Pair<Int, Int> {
        val legal = ranges.filter { it.first > 0 && it.second >= it.first }
        val exact = legal.filter { it.first == MOTION_TARGET_FPS && it.second == MOTION_TARGET_FPS }
        if (exact.isNotEmpty()) return exact.first()
        val covers = legal.filter { it.first <= MOTION_TARGET_FPS && it.second >= MOTION_TARGET_FPS }
        if (covers.isNotEmpty()) return covers.minBy { it.second - it.first }
        return legal.maxByOrNull { it.second } ?: (0 to 0)
    }
}
