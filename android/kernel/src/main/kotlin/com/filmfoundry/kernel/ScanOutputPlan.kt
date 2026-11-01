package com.filmfoundry.kernel

/** Frozen readout math after exact whole-frame statistics. No graphics API or media ownership.
 * A backend may execute this entire chain in one dispatch; null keeps this CPU reference.
 */
class ScanOutputPlan internal constructor(private val referenceNormalization: ScanNormalization?,
    val boundedInput: Boolean,val encoding: Encoding) {
    class Normalization internal constructor(val black: List<Float>,val spans: List<Float>,val strength: Float,val rgb: Boolean)
    class Encoding internal constructor(val printGain: Float,val lampColor: List<Float>?,val contrast: Float,
        val curves: List<List<Pair<Float,Float>>>?,val tone: List<Pair<Float,Float>>?) {
        // Keep the CPU loop's primitive/indexed storage; the backend-facing snapshot must not
        // introduce boxed List<Float> access for every channel when GPU is unavailable.
        internal val lampValues=lampColor?.toFloatArray()
        internal val curveValues=curves?.toTypedArray()
    }
    val normalization=referenceNormalization?.parameters()
    fun applyInPlace(values: FloatArray) {
        require(values.size%3==0)
        if(referenceNormalization!=null) referenceNormalization.apply(values,boundedInput=boundedInput)
        else if(boundedInput) PhoneObserve.boundNegative(values)
        PhoneObserve.encodeRows(values,encoding)
    }
    fun reference(values: FloatArray)=values.copyOf().also(::applyInPlace)
}
