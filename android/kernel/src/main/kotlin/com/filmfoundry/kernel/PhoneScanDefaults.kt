package com.filmfoundry.kernel

/** Read-only interpretation of the final medium. Never inferred from the original stock name. */
internal object PhoneScanDefaults {
    fun removeBase(positive: Boolean,base: FloatArray): Boolean =
        !positive || (base.maxOrNull()!!-base.minOrNull()!! > .015f)
}
