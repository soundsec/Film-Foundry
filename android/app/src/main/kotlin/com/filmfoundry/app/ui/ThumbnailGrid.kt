package com.filmfoundry.app.ui

import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.util.LruCache
import android.view.View
import android.widget.*
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/** Shared across the three grids. Eviction drops cache ownership, never recycles a visible bitmap. */
class DisplayThumbnailCache {
    val budget=(Runtime.getRuntime().maxMemory()/12).coerceIn(12L*1024*1024,40L*1024*1024).toInt()
    private val values=object: LruCache<String,Bitmap>(budget) { override fun sizeOf(key: String,value: Bitmap)=value.allocationByteCount }
    fun get(key: String): Bitmap?=values.get(key)?.let { if(it.isRecycled) { values.remove(key);null } else it }
    fun put(key: String,value: Bitmap) {
        // Full-resolution display images can exceed this entire cache. Never let an
        // oversized insertion evict the thumbnails needed for the next page transition.
        if(!value.isRecycled && value.allocationByteCount<=budget &&
            (!key.startsWith("display:") || value.allocationByteCount<=budget/4)) values.put(key,value)
    }
    fun remove(key: String) { values.remove(key) }
    fun clear()=values.evictAll()
    fun trim(critical: Boolean) { if(critical) clear() else values.trimToSize(budget/2) }
}

/** Visible tiles only, one decoder, bounded display cache. No full image decoding on UI thread. */
class ThumbnailGrid(private val shell: MobileShell,private val cache: DisplayThumbnailCache,private val canLoad: ()->Boolean): LinearLayout(shell.root.context) {
    data class Entry(val key: String,val title: String,val detail: String,val negative: Boolean=false,
        val load: (check: ()->Unit)->Bitmap,val open: ()->Unit,val bindSelection: ((View,CheckBox)->Unit)?=null,
        val information: ((check: ()->Unit)->String)?=null,val rotationDegrees: Int=0,val compact: String=detail)
    private data class Tile(val entry: Entry,val image: ImageView,val label: TextView,val detail: TextView,var informationReady: Boolean)
    private val executor=Executors.newSingleThreadExecutor()
    private val handler=Handler(Looper.getMainLooper())
    private val version=AtomicInteger()
    private val tiles=mutableListOf<Tile>()
    private val failed=mutableSetOf<String>()
    private var decoding=false;@Volatile private var closed=false
    private var wasShown=false
    private val pump=Runnable { loadNext() }
    var onEndReached: (() -> Unit)?=null
    private var endPosted=false
    init {
        orientation=VERTICAL
        viewTreeObserver.addOnScrollChangedListener { schedule() }
        viewTreeObserver.addOnGlobalLayoutListener {
            val visible=isShown && width>0 && height>0
            if(visible && !wasShown) { failed.clear();schedule() }
            wasShown=visible
            if(visible && !decoding && tiles.any { it.image.drawable==null && it.entry.key !in failed }) schedule()
        }
        addOnAttachStateChangeListener(object: OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(view: View) { failed.clear();schedule() }
            override fun onViewDetachedFromWindow(view: View) { wasShown=false;suspendLoading() }
        })
    }
    fun submit(entries: List<Entry>,columns: Int=3) {
        require(columns in listOf(1,3,5))
        version.incrementAndGet();removeAllViews();tiles.clear();failed.clear();endPosted=false
        for(rowEntries in entries.chunked(columns)) {
            val row=LinearLayout(context);addView(row,LayoutParams(-1,-2))
            for(entry in rowEntries) {
                val cell=LinearLayout(context).apply { orientation=if(columns==1) HORIZONTAL else VERTICAL;isClickable=true;isFocusable=true;contentDescription="${entry.title}, ${entry.detail}"
                    setOnClickListener { entry.open() } }
                row.addView(cell,LayoutParams(0,-2,1f).apply { setMargins(shell.dp(2),shell.dp(2),shell.dp(2),shell.dp(8)) })
                val frame=object: FrameLayout(context) { override fun onMeasure(w: Int,h: Int) { super.onMeasure(w,MeasureSpec.makeMeasureSpec(MeasureSpec.getSize(w),MeasureSpec.EXACTLY)) } }
                frame.setBackgroundColor(Color.rgb(38,37,34));cell.addView(frame,if(columns==1) LayoutParams(shell.dp(112),shell.dp(112)) else LayoutParams(-1,-2))
                val image=ImageView(context).apply { scaleType=if(entry.negative) ImageView.ScaleType.FIT_CENTER else ImageView.ScaleType.CENTER_CROP }
                image.addOnLayoutChangeListener { _,_,_,_,_,_,_,_,_ -> if(entry.rotationDegrees!=0) orientOutputView(image,entry.rotationDegrees,!entry.negative) }
                val label=TextView(context).apply { text=if(entry.negative) shell.copy("底片","Film") else "◌";setTextColor(Color.LTGRAY);gravity=android.view.Gravity.CENTER;textSize=12f }
                frame.addView(label,FrameLayout.LayoutParams(-1,-1));frame.addView(image,FrameLayout.LayoutParams(-1,-1))
                val selected=CheckBox(context).apply {
                    isClickable=false;isFocusable=false;importantForAccessibility=IMPORTANT_FOR_ACCESSIBILITY_NO
                    buttonTintList=android.content.res.ColorStateList.valueOf(0xFFE8BF6D.toInt())
                    setBackgroundColor(0xAA181816.toInt());visibility=GONE
                }
                frame.addView(selected,FrameLayout.LayoutParams(shell.dp(40),shell.dp(40),android.view.Gravity.TOP or android.view.Gravity.END))
                entry.bindSelection?.invoke(cell,selected)
                val textPanel=if(columns==1) LinearLayout(context).apply { orientation=VERTICAL;setPadding(shell.dp(12),0,0,0);cell.addView(this,LayoutParams(0,-2,1f)) } else cell
                shell.note(textPanel,entry.title,entry.title).apply { visibility=if(columns==1) VISIBLE else GONE;textSize=12f;maxLines=2;ellipsize=android.text.TextUtils.TruncateAt.END;setPadding(0,shell.dp(6),0,0) }
                val caption=if(columns==1) entry.detail else entry.compact
                val detail=shell.note(textPanel,caption,caption).apply { textSize=11f;maxLines=if(columns==1) 12 else 2;ellipsize=android.text.TextUtils.TruncateAt.END;setPadding(0,shell.dp(2),0,0) }
                tiles+=Tile(entry,image,label,detail,entry.information==null)
                cache.get(entry.key)?.let { image.setImageBitmap(it);if(entry.rotationDegrees!=0) orientOutputView(image,entry.rotationDegrees,!entry.negative);label.visibility=GONE }
            }
            repeat(columns-rowEntries.size) { row.addView(View(context),LayoutParams(0,1,1f)) }
        }
        schedule()
    }
    fun schedule() { handler.removeCallbacks(pump);if(!closed) handler.postDelayed(pump,80) }
    private fun loadNext() {
        if(closed || !isShown) return
        for(tile in tiles) {
            if(tile.image.getGlobalVisibleRect(Rect())) {
                val bitmap=(tile.image.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap
                if(bitmap?.isRecycled==true) tile.image.setImageDrawable(null)
                if(tile.image.drawable==null) cache.get(tile.entry.key)?.let {
                tile.image.setImageBitmap(it);if(tile.entry.rotationDegrees!=0) orientOutputView(tile.image,tile.entry.rotationDegrees,!tile.entry.negative);tile.label.visibility=GONE
                }
            }
        }
        retainNearbyImages()
        if(!endPosted && onEndReached!=null && tiles.lastOrNull()?.image?.getGlobalVisibleRect(Rect())==true) {
            endPosted=true;handler.post { if(!closed && isShown) onEndReached?.invoke() }
        }
        if(decoding || !canLoad()) return
        val pendingImage=tiles.firstOrNull { it.image.drawable==null && it.entry.key !in failed && it.image.getGlobalVisibleRect(Rect()) }
        val informationTile=if(pendingImage!=null) null else tiles.firstOrNull { !it.informationReady && it.image.getGlobalVisibleRect(Rect()) }
        if(informationTile!=null) {
            val token=version.get();decoding=true
            executor.execute {
                val result=runCatching { informationTile.entry.information!! { if(closed || token!=version.get()) throw InterruptedException("Information superseded") } }
                handler.post {
                    decoding=false
                    if(!closed && token==version.get()) {
                        informationTile.informationReady=true
                        val text=result.getOrElse { shell.copy("信息不可用","Information unavailable") }
                        informationTile.detail.text=text
                        (informationTile.image.parent.parent as View).contentDescription=informationTile.entry.title+", "+text
                    }
                    if(!closed) loadNext()
                }
            }
            return
        }
        val tile=pendingImage ?: return
        val token=version.get();decoding=true
        executor.execute {
            val result=runCatching { tile.entry.load { if(closed || token != version.get() || Thread.currentThread().isInterrupted) throw InterruptedException("Thumbnail superseded") } }
            handler.post {
                decoding=false
                if(!closed && token==version.get()) {
                    result.getOrNull()?.takeUnless { it.isRecycled }?.let { bitmap -> cache.put(tile.entry.key,bitmap);tile.image.setImageBitmap(bitmap);if(tile.entry.rotationDegrees!=0) orientOutputView(tile.image,tile.entry.rotationDegrees,!tile.entry.negative);tile.label.visibility=GONE }
                    if(tile.image.drawable==null) { failed+=tile.entry.key;tile.label.visibility=VISIBLE;tile.label.text=shell.copy("加载失败 · 点按打开","Unable to load · Tap to open") }
                } else result.getOrNull()?.let { if(!closed) cache.put(tile.entry.key,it) else it.recycle() }
                schedule()
            }
        }
    }
    /** Scrolling is not memory pressure. Retain nearby bindings within a shared-cache-sized budget. */
    private fun retainNearbyImages() {
        val visible=Rect();if(!getGlobalVisibleRect(visible)) return
        var retained=0L
        val offscreen=tiles.filter { !it.image.getGlobalVisibleRect(Rect()) }.sortedBy {
            val location=IntArray(2);it.image.getLocationOnScreen(location)
            kotlin.math.abs(location[1]+it.image.height/2-visible.centerY())
        }
        for(tile in offscreen) {
            val bitmap=(tile.image.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap ?: continue
            if(!bitmap.isRecycled) retained+=bitmap.allocationByteCount
            if(bitmap.isRecycled || retained>cache.budget/3) {
                tile.image.setImageDrawable(null);tile.label.visibility=VISIBLE
            }
        }
    }
    /** Leave the page without finishing every pending optical decode. */
    fun suspendLoading() {
        version.incrementAndGet();handler.removeCallbacks(pump)
        // Hidden pages retain their thumbnails. The shared budget or memory pressure evicts them.
    }
    fun trim() { tiles.filter { !isShown || !it.image.getGlobalVisibleRect(Rect()) }.forEach { it.image.setImageDrawable(null);it.label.visibility=VISIBLE } }
    fun close() { closed=true;version.incrementAndGet();handler.removeCallbacksAndMessages(null);executor.shutdownNow();removeAllViews();tiles.clear() }
}
