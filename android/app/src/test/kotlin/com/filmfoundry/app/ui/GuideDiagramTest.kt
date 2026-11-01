package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.awt.*
import java.awt.geom.*
import java.awt.image.BufferedImage
import java.io.File
import javax.imageio.ImageIO

class GuideDiagramTest {
    @Test fun helpOpensTheRelevantStageIncludingViewingAfterDevelopment() {
        for(page in listOf("start","capture","loading")) assertEquals(GuideTopic.CAPTURE,GuideTopic.forPage(page))
        for(page in listOf("rolls","roll_detail","develop")) assertEquals(GuideTopic.DEVELOP,GuideTopic.forPage(page))
        for(page in listOf("roll_batch","scan","viewer")) assertEquals(GuideTopic.VIEW,GuideTopic.forPage(page))
        assertEquals(GuideTopic.ORGANIZE,GuideTopic.forPage("observe"))
        assertEquals(GuideTopic.RESOURCES,GuideTopic.forPage("settings_process"))
    }
    @Test fun lessonsHaveDistinctOperationScenesAndCanBeReviewedFromTheActualVectorPlans() {
        val scenes=(0..4).map { GuideDiagram.scene(it,true) }
        assertEquals(5,scenes.distinct().size)
        assertTrue((scenes[1].filterIsInstance<GuideDiagram.Label>().map { it.value }).containsAll(listOf("DEV","BL","FIX")))
        assertTrue(scenes[2].any { it is GuideDiagram.Triangle })
        val dir=System.getenv("FF_GUIDE_REVIEW_DIR") ?: return
        val image=BufferedImage(1080,5*260,BufferedImage.TYPE_INT_RGB);val g=image.createGraphics()
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING,RenderingHints.VALUE_ANTIALIAS_ON)
        for(i in 0..4) for(variant in 0..1) {
            val light=variant==1;val drawing=GuideDiagram.scene(i,variant==0)
            val c=g.create() as Graphics2D;c.translate(variant*540,i*260+16);c.scale(1.5,1.5)
            for(mark in drawing) {
                c.color=Color(GuideDiagram.color(mark.ink,light),true);c.stroke=BasicStroke(1.6f)
                when(mark) {
                    is GuideDiagram.Box -> { val r=RoundRectangle2D.Float(mark.x,mark.y,mark.w,mark.h,mark.radius*2,mark.radius*2);if(mark.fill) c.fill(r) else c.draw(r) }
                    is GuideDiagram.Circle -> { val r=Ellipse2D.Float(mark.x-mark.r,mark.y-mark.r,mark.r*2,mark.r*2);if(mark.fill) c.fill(r) else c.draw(r) }
                    is GuideDiagram.Line -> c.draw(Line2D.Float(mark.x,mark.y,mark.x2,mark.y2))
                    is GuideDiagram.Triangle -> { val p=Path2D.Float();p.moveTo(mark.points[0],mark.points[1]);p.lineTo(mark.points[2],mark.points[3]);p.lineTo(mark.points[4],mark.points[5]);p.closePath();c.fill(p) }
                    is GuideDiagram.Label -> {
                        c.font=Font("Microsoft YaHei UI",Font.BOLD,12).deriveFont(mark.size)
                        val w=c.fontMetrics.stringWidth(mark.value).toFloat()
                        if(w>mark.width) c.font=c.font.deriveFont(mark.size*mark.width/w)
                        c.drawString(mark.value,mark.x-c.fontMetrics.stringWidth(mark.value)/2f,mark.y)
                    }
                }
            };c.dispose()
        };g.dispose()
        File(dir).mkdirs();ImageIO.write(image,"png",File(dir,"guide-scenes.png"))
    }
}
