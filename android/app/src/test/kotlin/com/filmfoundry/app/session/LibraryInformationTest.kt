package com.filmfoundry.app.session

import com.filmfoundry.app.ui.LibraryInfoPresentation
import com.filmfoundry.interchange.*
import com.filmfoundry.platform.SavedScan
import com.filmfoundry.workflow.DevelopedResult
import java.nio.file.Path
import java.time.ZoneId
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class LibraryInformationTest {
    @TempDir lateinit var root: Path
    @Test fun photoUsesActualFinalFilmAndMissingSourceNeverGuessesFromTheNegativeRecipe() {
        val file=root.resolve("film.npz").toFile()
        file.writeBytes(NpzMediumArchive().writeDevelopedOptical(DevelopedOpticalArchive(FloatNd(intArrayOf(3,4,3),FloatArray(36){.4f}),
            mapOf("image_polarity" to "positive","compatible_interpreters" to listOf("positive_transparency_scan")))))
        val result=DevelopedResult("r","roll",1,"film.npz",mapOf("film" to mapOf("image_polarity" to "negative")))
        val info=LibraryInformation(root.toFile());val saved=SavedScan("photo","r","roll",1,1280,853,"2026-10-29T01:02:00Z","",null)
        assertEquals("positive",info.photo(saved,result).polarity)
        val negative=info.negative(result);assertEquals(4,negative.width);assertTrue(negative.fileTime)
        assertTrue(file.delete());assertNull(info.photo(saved,result).polarity)
        assertEquals("positive",info.photo(saved.copy(sourcePolarity="positive"),null).polarity)
        assertEquals("2026-10-29 09:02",LibraryInfoPresentation.time(info.photo(saved,null),false,ZoneId.of("Asia/Shanghai")))
        assertTrue(LibraryInfoPresentation.brief(negative,false).contains("文件时间"))
        assertTrue(LibraryInfoPresentation.brief(info.photo(saved,null),false,photo=true).contains("未记录"))
    }
}
