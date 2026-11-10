package com.filmfoundry.kernel

import kotlin.math.max
import kotlin.math.min

/** Frozen whole-frame ranges. Contains no pixel arrays; application can use independent rows. */
internal class ScanNormalization private constructor(
    private val black: FloatArray,
    private val spans: FloatArray,
    private val strength: Float,
    private val rgb: Boolean,
) {
    fun parameters()=ScanOutputPlan.Normalization(black.toList(),spans.toList(),strength,rgb)
    fun apply(linear: FloatArray, startPixel: Int = 0, pixels: Int = linear.size / 3 - startPixel,boundedInput: Boolean=false) {
        require(startPixel >= 0 && pixels >= 0 && (startPixel.toLong() + pixels) * 3 <= linear.size)
        if (rgb) {
            for (channel in 0..2) {
                val span = spans[channel]
                if (span <= 1e-6f) { if(boundedInput) for(p in startPixel until startPixel+pixels) linear[p*3+channel]=bounded(linear[p*3+channel]);continue }
                for (pixel in startPixel until startPixel + pixels) {
                    val i = pixel * 3 + channel
                    val src = linear[i]
                    val stretched = ((src - black[channel]) / span).coerceIn(0f, 1f)
                    linear[i] = (if(boundedInput) bounded(src) else src) * (1f - strength) + stretched * strength
                }
            }
        } else {
            val span = spans[0]
            if (span <= 1e-6f) { if(boundedInput) for(i in startPixel*3 until (startPixel+pixels)*3) linear[i]=bounded(linear[i]);return }
            for (pixel in startPixel until startPixel + pixels) {
                val i = pixel * 3
                val y = 0.2126f * linear[i] + 0.7152f * linear[i + 1] + 0.0722f * linear[i + 2]
                val stretched = ((y - black[0]) / span).coerceIn(0f, 1f)
                val gain = if (y > 1e-6f) (if(boundedInput) stretched else y * (1f - strength) + stretched * strength) / y else 0f
                for (channel in 0..2) {
                    val src=linear[i+channel];val target=max(src*gain,0f)
                    linear[i+channel]=if(boundedInput) bounded(src)*(1f-strength)+target*strength else target
                }
            }
        }
    }

    /** First radix pass can consume freshly generated scan rows before they reach disk. */
    class RowMeasurement(private val pixels: Int,private val strength: Float,mode: String,
        blackPercentile: Float,whitePercentile: Float) {
        init { require(pixels>0) }
        private val rgb=mode=="rgb";private val channels=if(rgb) 3 else 1
        private val positions=floatArrayOf((blackPercentile.coerceIn(0f,100f)/100f)*(pixels-1),(whitePercentile.coerceIn(0f,100f)/100f)*(pixels-1))
        private val ranks=intArrayOf(positions[0].toInt(),minOf(positions[0].toInt()+1,pixels-1),positions[1].toInt(),minOf(positions[1].toInt()+1,pixels-1))
        private fun key(value: Float): Int { require(value.isFinite());val bits=value.toRawBits();return bits xor if(bits<0) -1 else Int.MIN_VALUE }
        private fun value(block: FloatArray,i: Int,c: Int)=if(rgb) block[i+c] else block[i]*.2126f+block[i+1]*.7152f+block[i+2]*.0722f
        private val high=Array(channels){IntArray(65536)}
        private var seen=0
        private var finished=false
        fun collect(block: FloatArray,startPixel: Int=0,count: Int=block.size/3-startPixel) {
            check(!finished)
            require(block.size%3==0 && startPixel>=0 && count>=0 && (startPixel.toLong()+count)*3<=block.size)
            require(seen.toLong()+count<=pixels)
            seen+=count
            for(i in startPixel*3 until (startPixel+count)*3 step 3) for(c in 0 until channels) high[c][key(value(block,i,c)) ushr 16]++
        }
        fun finish(eachBlock: ((FloatArray)->Unit)->Unit): ScanNormalization {
            check(!finished);require(seen==pixels);finished=true
            val prefixes=Array(channels){IntArray(4)};val offsets=Array(channels){IntArray(4)}
            val low=Array(channels){linkedMapOf<Int,IntArray>()}
            for(c in 0 until channels) for(r in ranks.indices) {
                var preceding=0;var prefix=0
                while(prefix<65535 && preceding+high[c][prefix]<=ranks[r]) { preceding+=high[c][prefix];prefix++ }
                prefixes[c][r]=prefix;offsets[c][r]=ranks[r]-preceding
                low[c].getOrPut(prefix){IntArray(65536)}
            }
            var secondCount=0L
            eachBlock { block -> require(block.size%3==0);secondCount+=block.size/3;require(secondCount<=pixels)
                for(i in block.indices step 3) for(c in 0 until channels) {
                val k=key(value(block,i,c));low[c][k ushr 16]?.let { it[k and 65535]++ }
            } }
            require(secondCount==pixels.toLong())
            val black=FloatArray(channels);val spans=FloatArray(channels)
            for(c in 0 until channels) {
                val selected=FloatArray(4) { r ->
                    val histogram=low[c].getValue(prefixes[c][r]);var n=offsets[c][r];var suffix=0
                    while(suffix<65535 && n>=histogram[suffix]) { n-=histogram[suffix];suffix++ }
                    val ordered=(prefixes[c][r] shl 16) or suffix
                    Float.fromBits(ordered xor if(ordered<0) Int.MIN_VALUE else -1)
                }
                val bf=positions[0]-ranks[0];val wf=positions[1]-ranks[2]
                black[c]=selected[0]*(1f-bf)+selected[1]*bf
                spans[c]=selected[2]*(1f-wf)+selected[3]*wf-black[c]
            }
            return ScanNormalization(black,spans,strength,rgb)
        }
    }

    companion object {
        fun bounded(value: Float): Float { val x=value.coerceAtLeast(0f);return x/(1f+x) }
        /** Exact IEEE float order statistics; the standalone path still reads both passes. */
        fun measureRows(pixels: Int,strength: Float,mode: String,blackPercentile: Float,whitePercentile: Float,
            eachBlock: ((FloatArray)->Unit)->Unit): ScanNormalization {
            val measurement=RowMeasurement(pixels,strength,mode,blackPercentile,whitePercentile)
            eachBlock { measurement.collect(it) }
            return measurement.finish(eachBlock)
        }
        fun measure(linear: FloatArray, pixels: Int, strength: Float, mode: String,
                    blackPercentile: Float, whitePercentile: Float,startPixel: Int=0): ScanNormalization {
            require(pixels > 0 && startPixel>=0 && (pixels.toLong()+startPixel) * 3 <= linear.size)
            val rgb = mode == "rgb"
            val black = FloatArray(if (rgb) 3 else 1)
            val spans = FloatArray(black.size)
            val samples = FloatArray(pixels)
            for (channel in black.indices) {
                for (pixel in 0 until pixels) samples[pixel] = if (rgb) linear[(pixel+startPixel) * 3 + channel] else
                    0.2126f * linear[(pixel+startPixel) * 3] + 0.7152f * linear[(pixel+startPixel) * 3 + 1] + 0.0722f * linear[(pixel+startPixel) * 3 + 2]
                val (lo, hi) = exactPair(samples, blackPercentile, whitePercentile)
                black[channel] = lo
                spans[channel] = hi - lo
            }
            return ScanNormalization(black, spans, strength, rgb)
        }

        /** Select the same four order statistics as sorting, preserving the old Float interpolation. */
        private fun exactPair(values: FloatArray, black: Float, white: Float): Pair<Float, Float> {
            val b = (black.coerceIn(0f, 100f) / 100f) * values.lastIndex
            val w = (white.coerceIn(0f, 100f) / 100f) * values.lastIndex
            val bi = b.toInt().coerceIn(0, values.lastIndex)
            val wi = w.toInt().coerceIn(0, values.lastIndex)
            val bj = min(bi + 1, values.lastIndex)
            val wj = min(wi + 1, values.lastIndex)
            val ranks = intArrayOf(bi, bj, wi, wj).sortedArray()
            var previous = -1
            for (rank in ranks) if (rank != previous) {
                select(values, rank, previous + 1)
                previous = rank
            }
            val bf = b - bi
            val wf = w - wi
            return (values[bi] * (1f - bf) + values[bj] * bf) to
                (values[wi] * (1f - wf) + values[wj] * wf)
        }

        /** Three-way partition; a bounded iteration count falls back to the JDK's exact sort. */
        private fun select(values: FloatArray, rank: Int, from: Int) {
            var left = from
            var right = values.lastIndex
            var remaining = 2 * (32 - Integer.numberOfLeadingZeros(right - left + 1))
            while (left < right) {
                if (right - left < 32 || remaining-- <= 0) {
                    java.util.Arrays.sort(values, left, right + 1)
                    return
                }
                val a = values[left]; val b = values[(left + right) ushr 1]; val c = values[right]
                val pivot = if (a.compareTo(b) < 0) {
                    if (b.compareTo(c) < 0) b else if (a.compareTo(c) < 0) c else a
                } else {
                    if (a.compareTo(c) < 0) a else if (b.compareTo(c) < 0) c else b
                }
                var low = left; var i = left; var high = right
                while (i <= high) {
                    val order = values[i].compareTo(pivot)
                    when {
                        order < 0 -> { swap(values, low, i); low++; i++ }
                        order > 0 -> { swap(values, i, high); high-- }
                        else -> i++
                    }
                }
                when {
                    rank < low -> right = low - 1
                    rank > high -> left = high + 1
                    else -> return
                }
            }
        }

        private fun swap(values: FloatArray, a: Int, b: Int) {
            val value = values[a]; values[a] = values[b]; values[b] = value
        }
    }
}
