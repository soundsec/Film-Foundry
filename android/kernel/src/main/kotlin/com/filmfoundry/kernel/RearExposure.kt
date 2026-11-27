package com.filmfoundry.kernel

import kotlin.math.pow
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.sqrt

/** Three-band transmission before stock sensitivity. Source rows remain caller-owned. */
class RearExposure private constructor(
    private val gains: FloatArray, private val blueToRed: Float, private val blueToGreen: Float,
    val record: Map<String,Any>, private val halfWidth: Double,
    private val wedge: Boolean, private val sceneGain: Float,
) {
    private val radius = maxOf(0, ceil(halfWidth - .5).toInt())
    val needsRows get() = radius > 0 || record["mirror"] == "vertical"

    /** Read a physical halo and reflect in full-frame coordinates, including across tile boundaries. */
    fun bind(source: LinearRgbRows): LinearRgbRows = object: LinearRgbRows by source {
        override fun readRows(start: Int, count: Int): RgbVolume {
            val vertical = record["mirror"] == "vertical"
            val first = if (vertical) height - start - count else start
            val lo = maxOf(0, first - radius)
            val hi = minOf(height, first + count + radius)
            val input = source.readRows(lo, hi - lo)
            if (!needsRows) return transmitBlock(input)
            val stride = width * 3
            val rows = if (radius > 0) FloatArray(input.linearRgb.size) else input.linearRgb
            if (radius > 0) for (y in lo until hi) {
                source.checkActive()
                for (c in 0..2) filterLine(input.linearRgb,(y-lo)*stride+c,3,width,0,width,rows,(y-lo)*stride+c,3)
            }
            val block = RgbVolume(width,count)
            if (radius == 0) {
                for (y in 0 until count) rows.copyInto(block.linearRgb,(count-1-y)*stride,y*stride,(y+1)*stride)
            } else {
                val dest = if (vertical) (count-1)*stride else 0
                for (i in 0 until stride) {
                    if (i % 256 == 0) source.checkActive()
                    filterLine(rows,i,stride,hi-lo,first-lo,count,block.linearRgb,dest+i,if(vertical) -stride else stride)
                }
            }
            return transmitOwned(block)
        }
    }

    /** Sliding sum with fractional end pixels. Replicate the physical frame boundary. */
    private fun filterLine(src: FloatArray, offset: Int, step: Int, length: Int, first: Int, count: Int,
                           dst: FloatArray, dest: Int, destStep: Int) {
        val edge = halfWidth - radius + .5
        fun at(x: Int) = src[offset+x.coerceIn(0,length-1)*step].toDouble()
        var sum = 0.0
        for (x in first-radius+1 until first+radius) sum += at(x)
        for (i in 0 until count) {
            val x = first + i
            val right = at(x+radius)
            dst[dest+i*destStep] = ((sum+edge*(at(x-radius)+right))/(2*halfWidth)).toFloat()
            sum += right-at(x-radius+1)
        }
    }

    fun transmitBlock(input: RgbVolume) = transmitOwned(RgbVolume(input.width,input.height,linearRgb=input.linearRgb.copyOf()))

    private fun transmitOwned(input: RgbVolume): RgbVolume {
        val rgb = input.linearRgb
        if (record["mirror"] == "horizontal") {
            for (y in 0 until input.height) for (x in 0 until input.width/2) for (c in 0..2) {
                val left = (y*input.width+x)*3+c
                val right = (y*input.width+input.width-1-x)*3+c
                val tmp = rgb[left]; rgb[left] = rgb[right]; rgb[right] = tmp
            }
        }
        val table = if (wedge) doses else null
        for (i in rgb.indices step 3) {
            val blue = rgb[i+2]
            if (table != null) {
                val b = blue*gains[2]*sceneGain
                val g = rgb[i+1]*gains[1]*sceneGain
                rgb[i] = drive(.90f*rgb[i]*gains[0]*sceneGain + .10f*b,table[0],table[1])/sceneGain
                rgb[i+1] = drive(.965f*g+.035f*b,table[0],table[2])/sceneGain
                rgb[i+2] = drive(b,table[0],table[3])/sceneGain
            } else {
                rgb[i] = rgb[i]*gains[0] + blue*blueToRed
                rgb[i+1] = rgb[i+1]*gains[1] + blue*blueToGreen
                rgb[i+2] = blue*gains[2]
            }
        }
        return input
    }

    companion object {
        private val doses by lazy {
            val rows = checkNotNull(RearExposure::class.java.getResourceAsStream("/harman_red_125_wedge.txt"))
                .bufferedReader().useLines { lines -> lines.filter { it.isNotBlank() && !it.startsWith("#") }
                    .map { it.split(' ') }.toList() }
            listOf(rows.map { (.18*10.0.pow(it[0].toDouble()-2.643)).toFloat() }.toFloatArray()) +
                (4..6).map { c -> rows.map { it[c].toFloat() }.toFloatArray() }
        }
        private fun drive(q: Float, knots: FloatArray, values: FloatArray): Float {
            if (q <= knots.first()) return q*(values.first()/knots.first())
            val found = knots.binarySearch(q)
            if (found >= 0) return values[found]
            val i = minOf(-found-1,knots.lastIndex)
            return values[i-1]+(q-knots[i-1])*(values[i]-values[i-1])/(knots[i]-knots[i-1])
        }
        fun from(recipe: Map<String,Any?>, width: Int, height: Int): RearExposure? {
            val exposure = recipe["exposure"] as? Map<*,*> ?: return null
            if ("film_side" !in exposure || exposure["film_side"] == "emulsion") return null
            require(exposure["film_side"] == "base") { "Unknown film_side" }
            val opt = exposure["rear_optics"]?.let { it as Map<*,*> }.orEmpty()
            require(opt.keys.all { it in setOf("mirror","profile","thickness_um","index","f_number","focus_shift_um","auto_refocus") })
            fun parameter(key: String, default: Double, range: ClosedFloatingPointRange<Double>): Double {
                val v = if (key in opt) (opt[key] as Number).toDouble() else default
                require(v in range) { "Invalid rear optics: $key" }
                return v
            }
            val mirror = opt["mirror"] ?: "horizontal"
            val profile = opt["profile"] ?: "generic"
            require(mirror in listOf("horizontal","vertical","none") && profile in listOf("generic","harman_red_125"))
            val thickness = parameter("thickness_um",125.0,10.0..350.0)
            val n = parameter("index",1.48,1.0..2.0)
            val aperture = parameter("f_number",5.6,.7..128.0)
            val shift = parameter("focus_shift_um",0.0,-500.0..500.0)
            val auto = if ("auto_refocus" in opt) opt["auto_refocus"] as Boolean else false
            val record = mapOf("mirror" to mirror,"profile" to profile,"thickness_um" to thickness,"index" to n,
                "f_number" to aperture,"focus_shift_um" to shift,"auto_refocus" to auto)
            val develop = (recipe["chemistry"] as? Map<*,*>).orEmpty().entries.associate { it.key to it.value } +
                (recipe["develop"] as? Map<*,*>).orEmpty().entries.associate { it.key to it.value }
            val diameter = if (auto) 0.0 else abs(thickness/n-shift) * maxOf(width,height) / (1000*aperture*PhoneMaterialGeometry.frameLength(develop))
            val film = (recipe["film"] as? Map<*,*>).orEmpty()
            val support = permanentSupport(film, recipe["image_polarity"] ?: recipe["medium"])
            val backing = film["carbon_backing"] as? Map<*,*>
            val carbon = if (backing == null) 0.0 else {
                val mass = (backing["initial_mg_m2"] as? Number)?.toDouble() ?: 40.0
                val density = (backing["density_per_mg_m2"] as? Number)?.toDouble() ?: .02
                require(mass in 0.0..1000.0 && density in 0.0..10.0)
                mass*density
            }
            val transmission = support.map { d -> 10.0.pow(-d.toDouble()-carbon) }
            val mono = film["color_process"]?.toString()?.lowercase() in
                setOf("bw","black_white","monochrome","chromogenic_monochrome")
            val wedge = !mono && profile=="harman_red_125"
            val stops = if (wedge) DoubleArray(3) else if (mono) doubleArrayOf(1.0,1.0,1.0) else doubleArrayOf(.65,2.15,4.25)
            val ev = ((recipe["look"] as? Map<*,*>)?.get("exposure_ev") as? Number)?.toDouble() ?: 0.0
            val referenceTransmission = if(wedge) 10.0.pow(-.03) else 1.0
            return RearExposure(FloatArray(3) { (transmission[it]*2.0.pow(-stops[it])/referenceTransmission).toFloat() },
                if (mono) 0f else (.10*transmission[2]).toFloat(),
                if (mono) 0f else (.035*transmission[2]).toFloat(),record,diameter*sqrt(3.0)/4,wedge,2.0.pow(ev).toFloat())
        }

        /** Factory and edge lamps have their own light path. Keep other capture settings. */
        fun frontRecipe(recipe: Map<String,Any?>): Map<String,Any?> {
            val exposure = recipe["exposure"] as? Map<*,*> ?: return recipe
            if ("film_side" !in exposure) return recipe
            return recipe + ("exposure" to exposure.filterKeys { it != "film_side" && it != "rear_optics" })
        }
    }
}
