package com.filmfoundry.interchange

/** 128x9 fixed plate; matches Python factory_label_pattern. Decorative, not DX/KeyKode. */
object FactoryLabelPattern {
    fun create(text: String,batch: Int=0): EdgeMarkPattern {
        val label=text.trim().uppercase(java.util.Locale.ROOT)
        require(label.length in 1..14 && label.all { it in 'A'..'Z' || it in '0'..'9' || it==' ' || it=='-' })
        require(batch in 0..4095)
        val cells=BooleanArray(128*9)
        fun put(x: Int,y: Int) { cells[y*128+x]=true }
        for((index,ch) in label.withIndex()) for(y in 0..6) for(x in 0..4)
            if(ExposureGlyphs.lit(ch,x,y)) put(2+index*6+x,1+y)
        for(x in listOf(92,94,125)) for(y in 0..8) put(x,y)
        for(i in 0..11) for(y in 1..7) {
            put(98+2*i,y)
            if(batch and (1 shl (11-i))!=0) put(99+2*i,y)
        }
        return EdgeMarkPattern.fromCells(128,9,cells)
    }
}
