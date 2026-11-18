package com.filmfoundry.app.ui

import android.graphics.*
import com.filmfoundry.interchange.StillFrameFormat
import kotlin.math.min

/** The open-back camera drawn for loading and for taking the roll out. Pose only; no gestures. */
internal class FilmChamberPicture(private val format: String, private val face: FilmFace.Palette) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rect = RectF()
    private val path = Path()
    private val metal = LinearGradient(0f, 0f, 0f, 520f, intArrayOf(0xff5a5951.toInt(), 0xff252622.toInt(), 0xff101210.toInt()), null, Shader.TileMode.CLAMP)
    private val cassette = LinearGradient(90f, 0f, 260f, 0f, intArrayOf(0xff10120f.toInt(), 0xff66675b.toInt(), 0xff131511.toInt()), null, Shader.TileMode.CLAMP)
    private val film = LinearGradient(0f, 190f, 0f, 410f, intArrayOf(face.edge, face.body, face.core), null, Shader.TileMode.CLAMP)
    private val rails = floatArrayOf(167f, 188f, 423f, 446f)
    private val perforationRows = floatArrayOf(208f, 389f)
    var thread = 0f
    var approach = 0f
    /** 1 is the back fully open. Loading leaves this open. */
    var door = 1f
    /** 1 lifts the cassette out of the chamber. */
    var removed = 0f
    var scale = 1f
        private set
    var originX = 0f
        private set
    var originY = 0f
        private set

    fun draw(canvas: Canvas, width: Int, height: Int) {
        scale = min(width / 1000f, height / 520f).coerceAtLeast(.01f)
        originX = (width - 1000f * scale) / 2
        originY = (height - 520f * scale) / 2
        canvas.save()
        canvas.translate(originX, originY)
        canvas.scale(scale, scale)
        val t = (approach / .84f).coerceIn(0f, 1f)
        val zoom = 1f + 15f * t * t
        canvas.translate(0f, 190f * t)
        canvas.scale(zoom, zoom, 500f, 70f)
        box(canvas, 30f, 15f, 970f, 510f, 28f, 0xff151714.toInt(), metal)
        box(canvas, 58f, 145f, 942f, 466f, 17f, Color.BLACK)
        box(canvas, 412f, 0f, 588f, 128f, 18f, 0xff57594f.toInt())
        box(canvas, 431f, 15f, 569f, 111f, 12f, 0xff080b09.toInt())
        box(canvas, 444f, 29f, 556f, 95f, 8f, 0xff1e2924.toInt())
        line(canvas, 456f, 87f, 545f, 87f, 3f, 0xff718779.toInt())
        box(canvas, 284f, 193f, 775f, 416f, 5f, 0xff393b32.toInt())
        box(canvas, 327f, 208f, 718f, 400f, 1f, 0xff171c18.toInt())
        for (y in rails) {
            line(canvas, 278f, y, 781f, y, 7f, 0xff9b9b85.toInt())
            line(canvas, 278f, y - 3, 781f, y - 3, 2f, 0xffd1ccb4.toInt())
        }
        val lifting = removed > 0f
        if (lifting) {
            paint.alpha = ((1f - removed).coerceIn(0f, 1f) * 255).toInt()
            canvas.saveLayer(0f, 0f, 1000f, 520f, paint)
            canvas.translate(0f, -removed * 240f)
            paint.alpha = 255
        }
        box(canvas, 87f, 161f, 260f, 455f, 26f, 0xff30352b.toInt(), cassette)
        if (StillFrameFormat.is120(format)) {
            box(canvas, 76f, 161f, 270f, 180f, 5f, 0xff0a0e0b.toInt())
            box(canvas, 76f, 436f, 270f, 455f, 5f, 0xff0a0e0b.toInt())
        }
        box(canvas, 107f, 182f, 239f, 433f, 8f, face.label)
        FilmFace.drawMotif(canvas, paint, 107f, 250f, 239f, 420f, face.motif, face.ink)
        canvas.save()
        canvas.rotate(-90f, 173f, 308f)
        text(canvas, "FILM FOUNDRY", 173f, 300f, 20f, face.ink)
        text(canvas, if (StillFrameFormat.is120(format)) "120" else "135", 173f, 329f, 28f, face.ink)
        canvas.restore()
        box(canvas, 817f, 174f, 910f, 451f, 18f, 0xff575c4d.toInt())
        box(canvas, 831f, 226f, 896f, 417f, 5f, 0xffb7b69c.toInt())
        for (i in 0..10) line(canvas, 835f + i * 5f, 229f, 835f + i * 5f, 414f, 2f, 0xff747c69.toInt())
        val end = 475f + 385f * thread
        path.reset()
        path.moveTo(246f, 199f)
        path.lineTo(end - 46f, 199f)
        path.cubicTo(end - 48f, 247f, end - 70f, 263f, end, 284f)
        path.lineTo(end, 411f)
        path.lineTo(246f, 411f)
        path.close()
        paint.shader = film
        paint.style = Paint.Style.FILL
        canvas.drawPath(path, paint)
        paint.shader = null
        if (!StillFrameFormat.is120(format)) {
            canvas.save()
            canvas.clipPath(path)
            var x = 260f - ((thread * 385f) % 31f)
            while (x < end) {
                for (y in perforationRows) box(canvas, x, y, x + 17f, y + 14f, 3f, 0xff0a100b.toInt())
                x += 31f
            }
            canvas.restore()
        }
        if (thread < .97f && approach == 0f && door > .98f && removed == 0f) {
            line(canvas, end - 72f, 333f, end - 12f, 333f, 3f, 0xffeee3bd.toInt())
            line(canvas, end - 25f, 320f, end - 12f, 333f, 3f, 0xffeee3bd.toInt())
            line(canvas, end - 25f, 346f, end - 12f, 333f, 3f, 0xffeee3bd.toInt())
        }
        if (lifting) canvas.restore()
        if (door < 0.999f) {
            val bottom = 145f + (466f - 145f) * (1f - door)
            box(canvas, 58f, 145f, 942f, bottom.coerceAtLeast(153f), 12f, 0xff3c4036.toInt(), metal)
            line(canvas, 86f, 145f, 914f, 145f, 4f, 0xffd1ccb4.toInt())
        }
        canvas.restore()
        if (approach > .78f) {
            paint.shader = null
            paint.color = Color.BLACK
            paint.alpha = (((approach - .78f) / .13f).coerceIn(0f, 1f) * 255).toInt()
            canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), paint)
            paint.alpha = 255
        }
    }

    private fun box(c: Canvas, l: Float, t: Float, r: Float, b: Float, radius: Float, color: Int, shader: Shader? = null) {
        paint.style = Paint.Style.FILL
        paint.color = color
        paint.shader = shader
        rect.set(l, t, r, b)
        c.drawRoundRect(rect, radius, radius, paint)
        paint.shader = null
    }

    private fun line(c: Canvas, x: Float, y: Float, x2: Float, y2: Float, w: Float, color: Int) {
        paint.color = color
        paint.shader = null
        paint.strokeWidth = w
        paint.style = Paint.Style.STROKE
        c.drawLine(x, y, x2, y2, paint)
        paint.style = Paint.Style.FILL
    }

    private fun text(c: Canvas, s: String, x: Float, y: Float, size: Float, color: Int) {
        paint.color = color
        paint.textAlign = Paint.Align.CENTER
        paint.textSize = size
        paint.typeface = Typeface.DEFAULT_BOLD
        c.drawText(s, x, y, paint)
    }
}
