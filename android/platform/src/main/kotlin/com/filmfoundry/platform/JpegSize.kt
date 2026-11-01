package com.filmfoundry.platform

import java.io.File
import java.io.InputStream

/** JPEG pixel size from the SOF marker. Orientation is not read. */
object JpegSize {
    fun read(file: File): Pair<Int, Int> = file.inputStream().use { read(it) }

    fun read(input: InputStream): Pair<Int, Int> {
        val marker = ByteArray(2)
        require(input.read(marker) == 2 && marker[0] == 0xFF.toByte() && marker[1] == 0xD8.toByte()) { "invalid JPEG" }
        val header = ByteArray(4)
        while (true) {
            require(input.read(header) == 4 && header[0] == 0xFF.toByte()) { "truncated JPEG" }
            val type = header[1].toInt() and 0xFF
            val length = ((header[2].toInt() and 0xFF) shl 8) or (header[3].toInt() and 0xFF)
            require(length >= 2) { "invalid JPEG segment" }
            if (type == 0xC0 || type == 0xC1 || type == 0xC2) {
                val body = ByteArray(5)
                require(input.read(body) == 5) { "truncated JPEG frame" }
                val height = ((body[1].toInt() and 0xFF) shl 8) or (body[2].toInt() and 0xFF)
                val width = ((body[3].toInt() and 0xFF) shl 8) or (body[4].toInt() and 0xFF)
                require(width > 0 && height > 0) { "invalid JPEG size" }
                return width to height
            }
            var left = (length - 2).toLong()
            while (left > 0) {
                val skipped = input.skip(left)
                if (skipped > 0) left -= skipped
                else {
                    require(input.read() >= 0) { "truncated JPEG segment" }
                    left -= 1
                }
            }
        }
    }
}
