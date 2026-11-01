package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.hardware.camera2.CameraCharacteristics as C
import android.text.InputType
import android.view.View
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.platform.NativeCameraCatalog
import com.filmfoundry.platform.NativeCaptureSettings

/** Device controls stay separate from film defects and processing recipes. */
fun showNativeCameraDialog(activity: ComponentActivity,shell: MobileShell,lenses: List<NativeCameraCatalog.Lens>,
    selected: NativeCameraCatalog.Lens,settings: NativeCaptureSettings,shooting: Boolean=false,isoUnlocked: Boolean=false,
    apply: (NativeCameraCatalog.Lens,NativeCaptureSettings,Boolean)->Unit) {
    fun text(zh: String,en: String)=shell.copy(zh,en)
    val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(16),shell.dp(8),shell.dp(16),shell.dp(16)) }
    val picker=Spinner(activity).apply { minimumHeight=shell.dp(48);adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,lenses.map { it.label(activity.resources.configuration.locales[0].language=="zh") }) }
    panel.addView(picker)
    shell.note(panel,"自动镜头由系统选择或融合；固定镜头按系统开放能力使用。切换不改变胶卷，采集期间不能切换。","Auto lenses may switch or fuse in the system. Fixed lenses depend on device access. The roll stays loaded; switching is disabled during capture.")
    val body=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL };panel.addView(body)
    var read: ()->NativeCaptureSettings={ settings }
    var forceUnlock=false
    fun populate(lens: NativeCameraCatalog.Lens,initial: NativeCaptureSettings) {
        body.removeAllViews();val chars=lens.characteristics
        shell.note(body,"${lens.sizes().size} 种 JPEG 尺寸 · RAW ${if(lens.rawCapture) "可留存 DNG" else "当前选择不可用"} · Ultra HDR ${if(lens.ultraHdr) "支持" else "不可用"}",
            "${lens.sizes().size} JPEG sizes · RAW ${if(lens.rawCapture) "DNG capture available" else "unavailable for this selection"} · Ultra HDR ${if(lens.ultraHdr) "available" else "unavailable"}")
        var target=body
        fun spinner(zh: String,en: String,labels: List<String>): Spinner {
            shell.note(target,zh,en);return Spinner(activity).also { it.minimumHeight=shell.dp(48);it.adapter=ArrayAdapter(activity,android.R.layout.simple_spinner_dropdown_item,labels);target.addView(it) }
        }
        fun check(zh: String,en: String,value: Boolean,enabled: Boolean=true)=CheckBox(activity).also {
            it.text=text(zh,en);it.isChecked=value && enabled;it.isEnabled=enabled;target.addView(it)
            if(!enabled) { it.visibility=View.GONE;shell.note(target,"$zh · 本镜头不支持","$en · unavailable on this lens") }
        }
        fun number(zh: String,en: String,value: String)=EditText(activity).also { it.inputType=InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL;it.setText(value);shell.field(target,it,text(zh,en),resettable=false) }
        val formats=listOf("jpeg")+(if(lens.ultraHdr) listOf("ultra_hdr") else emptyList())+(if(lens.rawCapture) listOf("raw") else emptyList())
        val format=spinner("采集格式","Capture format",formats.map { when(it) { "raw"->"RAW / DNG";"ultra_hdr"->"Ultra HDR";else->"JPEG" } }).apply { setSelection(formats.indexOf(initial.format).coerceAtLeast(0)) }
        shell.note(body,"RAW 保留传感器原片，冲洗时再解码；仅支持已实现的未压缩 Bayer DNG。需装填相机原生精度或匹配 RAW 尺寸的静照卷，未知格式保留原片并提示。","RAW retains the sensor file for deferred development. The current decoder supports uncompressed Bayer DNG. Use native or RAW-compatible still resolution; unsupported files remain retained.")
        shell.note(body,"Ultra HDR 保留原文件，形成潜影时恢复增益图高光；目前支持 sRGB 基图。缺少增益图或不支持的色彩空间会提示，原片保留。它不是 RAW，也不是应用内多帧 HDR 合成；扫描输出仍为普通照片。","Ultra HDR keeps the original and reconstructs gain-map highlights during latent formation; sRGB base images are supported. Missing gain maps or unsupported color spaces are reported while originals are kept. This is neither RAW nor in-app HDR bracketing; scans remain standard photos.")
        val flashAvailable=chars.get(C.FLASH_INFO_AVAILABLE)==true
        val flash=spinner("照片闪光","Photo flash",listOf(text("关闭","Off"),text("自动","Auto"),text("开启","On"))).apply {
            isEnabled=flashAvailable;setSelection(if(flashAvailable) listOf("off","auto","on").indexOf(initial.flash).coerceAtLeast(0) else 0)
        }
        val torch=check("常亮补光（与闪光分开使用）","Continuous torch (separate from photo flash)",initial.torch,flashAvailable)
        val isoStatus=TextView(activity);body.addView(isoStatus)
        val manualSupported=(chars.get(C.REQUEST_AVAILABLE_CAPABILITIES) ?: intArrayOf()).contains(C.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR)
        val isoRange=chars.get(C.SENSOR_INFO_SENSITIVITY_RANGE);val timeRange=chars.get(C.SENSOR_INFO_EXPOSURE_TIME_RANGE)
        val unlock=if(shooting && manualSupported) check("强制手动解锁 ISO","Force-unlock ISO",isoUnlocked) else null
        val manualBox=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=if(unlock?.isChecked==true) View.VISIBLE else View.GONE }
        body.addView(manualBox)
        val previousTarget=target;target=manualBox
        val iso=number("ISO ${isoRange ?: ""}","ISO ${isoRange ?: ""}",initial.iso?.toString() ?: (isoRange?.lower ?: 100).toString())
        val time=number("快门时间（毫秒）","Shutter time (milliseconds)",initial.exposureNs?.let { (it/1e6).toString() } ?: "10")
        target=previousTarget
        shell.note(body,when {
            !manualSupported -> "这颗镜头不能手动指定 ISO，保持自动锁定。"
            shooting -> "平常在胶卷设置里指定 ISO。这里默认锁定；勾选强制手动解锁后，只在这次拍摄中临时生效。"
            else -> "ISO 在胶卷设置里指定，这里不能改。"
        },when {
            !manualSupported -> "This lens cannot set ISO manually. It stays automatic and locked."
            shooting -> "ISO is normally set in film settings and stays locked here. Force-unlock applies only for this shoot."
            else -> "ISO is set in film settings and cannot be changed here."
        })
        shell.note(body,"自动对焦与自动白平衡由相机负责。点按取景画面对焦测光；MF、手动白平衡暂不可用。","The camera controls autofocus and automatic white balance. Tap the viewfinder to focus and meter. MF and manual white balance are temporarily unavailable.")
        val expert=check("专业控制","Pro controls",initial.lockAe)
        val advanced=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=if(expert.isChecked) View.VISIBLE else View.GONE };body.addView(advanced)
        expert.setOnCheckedChangeListener { _,checked->advanced.visibility=if(checked) View.VISIBLE else View.GONE }
        target=advanced
        val ae=check("锁定自动曝光 AE","Lock auto exposure (AE)",initial.lockAe,chars.get(C.CONTROL_AE_LOCK_AVAILABLE)==true)
        fun update() {
            val manualOn=unlock?.isChecked==true
            manualBox.visibility=if(manualOn) View.VISIBLE else View.GONE
            val shown=if(manualOn) text("拍摄中手动","Manual for this shoot") else if(initial.iso==null) text("自动，已锁定","Automatic, locked") else "ISO ${initial.iso} · ${initial.exposureNs!!/1_000_000.0} ms"
            isoStatus.text=text("ISO：$shown","ISO: $shown")
            flash.isEnabled=flashAvailable && !manualOn && !torch.isChecked
            ae.isEnabled=!manualOn && chars.get(C.CONTROL_AE_LOCK_AVAILABLE)==true
        }
        unlock?.setOnCheckedChangeListener { _,on->if(on) { ae.isChecked=false;flash.setSelection(0) };update() }
        torch.setOnCheckedChangeListener { _,on->if(on) flash.setSelection(0);update() }
        update()
        read={
            val manualOn=unlock?.isChecked==true
            forceUnlock=manualOn
            val sensitivity=if(manualOn) iso.text.toString().toIntOrNull()?.also { require(isoRange?.contains(it)==true) { text("ISO 超出设备范围","ISO is outside the device range") } } ?: error(text("请输入整数 ISO","Enter an integer ISO")) else initial.iso
            val millis=if(manualOn) time.text.toString().toDoubleOrNull() else null
            val ns=if(manualOn) {
                require(millis!=null && millis.isFinite() && millis>0 && millis*1e6<Long.MAX_VALUE) { text("快门时间无效","Invalid shutter time") }
                (millis*1e6).toLong().also { require(timeRange?.contains(it)==true) { text("快门超出设备范围","Shutter time outside device range") } }
            } else initial.exposureNs
            NativeCaptureSettings(flash=listOf("off","auto","on")[if(flash.isEnabled) flash.selectedItemPosition else 0],torch=torch.isChecked,
                lockAe=ae.isChecked && ae.isEnabled,iso=sensitivity,exposureNs=ns,format=formats[format.selectedItemPosition])
        }
        shell.decorate(body)
    }
    picker.setSelection(lenses.indexOfFirst { it.key==selected.key }.coerceAtLeast(0))
    populate(selected,settings)
    picker.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
        var previous=selected.key
        override fun onNothingSelected(parent: AdapterView<*>?)=Unit
        override fun onItemSelected(parent: AdapterView<*>?,view: View?,position: Int,id: Long) { val lens=lenses[position];if(previous!=lens.key) { previous=lens.key;populate(lens,NativeCaptureSettings()) } }
    }
    val dialog=AlertDialog.Builder(activity).setTitle(text("镜头与原生相机","Lenses & native camera")).setView(ScrollView(activity).apply { addView(panel) })
        .setNegativeButton(text("取消","Cancel"),null).setPositiveButton(text("应用","Apply"),null).create()
    dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
        runCatching { read() }.onSuccess { value -> dialog.dismiss();apply(lenses[picker.selectedItemPosition],value,forceUnlock) }
            .onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
    } };shell.decorate(panel);dialog.show()
}
