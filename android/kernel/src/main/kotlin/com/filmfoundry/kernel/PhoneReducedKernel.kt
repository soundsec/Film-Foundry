package com.filmfoundry.kernel

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.FloatNd
import com.filmfoundry.interchange.SpectralOptics
import com.filmfoundry.interchange.PhoneMaterialPhenomena
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/**
 * Phone reduced formation. Same stage topology as desktop:
 * material pools -> exposure/latent -> process -> optical master -> read-only scan.
 *
 * Independent, versioned phone models: material response and optional spatial
 * exposure, ordered baths (or frozen legacy development), then optical pools.
 * The source binds its film parameters; observation reads the formed medium.
 */
class PhoneReducedKernel : RowReducedKernel, ObservationPreparingKernel {
    override fun prepareObservation(optical: OpticalMaster,recipe: Map<String,Any?>,checkCancel: ()->Unit): PreparedObservation =
        PhoneObserve.capture(optical,recipe,baseDensity(recipe),checkCancel=checkCancel,retainTransport=true)
    fun usesScannerDeviceResponse(recipe: Map<String,Any?>): Boolean {
        val mode=recipe["observe_mode"]?.toString() ?: "scanner"
        require(mode in DataKernel.PHONE_OBSERVE_MODES)
        return mode=="scanner" && PhoneScannerCapture(mapOf(recipe,"scanner"),recipe).active
    }
    override fun expose(linearRgb: RgbVolume, recipe: Map<String, Any?>): LatentState {
        return exposeRows(linearRgb, recipe, linearRgb.height)
    }

    override fun exposeRows(linearRgb: RgbVolume, recipe: Map<String, Any?>, fullHeight: Int): LatentState {
        require(fullHeight >= linearRgb.height) { "fullHeight must contain the row block" }
        return prepareExposure(recipe, linearRgb.width, fullHeight).exposeRows(linearRgb)
    }

    override fun prepareExposure(recipe: Map<String, Any?>, width: Int, fullHeight: Int): PreparedRowExposure {
        require(width > 0 && fullHeight > 0) { "frame dimensions must be positive" }
        val colorProcess = mapOf(recipe, "film")?.get("color_process")?.toString()?.trim()?.lowercase()
        val subtractive = booleanOf(recipe["enable_subtractive"], true) &&
            colorProcess !in listOf("bw", "black_white", "monochrome")
        val layers = if (subtractive) 3 else 1
        val geometry = PhoneMaterialGeometry.resolve(recipe, fullHeight, width)
        val materialSpectrum = mapOf(recipe, "film")?.get(SpectralOptics.MATERIAL_KEY)?.let { SpectralOptics.material(it) }
        val spectral = mapOf(recipe, "film")?.get(DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY)?.let {
            PhoneSpectralExposure.from(it, layers)
        }
        val ev = numberOf(mapOf(recipe, "look")?.get("exposure_ev"), 0f)
        val contrast = numberOf(mapOf(recipe, "film")?.get("contrast"), 1.08f).coerceIn(0.25f, 3f)
        val half = HALF_SATURATION / contrast
        val gain = 2.0.pow(ev.toDouble()).toFloat()
        val sensitivity = spectral?.matrix ?: matrix3(mapOf(recipe, "film"), "layer_sensitivity_matrix", DEFAULT_SENSITIVITY)
            .map { it.copyOf() }.toTypedArray()
        val metadata = latentMetadata(recipe, layers, subtractive).toMutableMap()
        val film=mapOf(recipe,"film").orEmpty()
        val boundFilm=film.toMutableMap().apply {
            putIfAbsent("image_polarity",metadata["image_polarity"])
            putIfAbsent("medium_family",metadata["medium_family"])
            putIfAbsent("medium_process",metadata["medium_process"])
            if(!subtractive) put("color_process","monochrome") else putIfAbsent("color_process","color")
        }
        metadata[DataKernel.MATERIAL_SNAPSHOT_KEY]=com.filmfoundry.interchange.MaterialSnapshot.capture(boundFilm)
        val extended=recipe[PhoneMaterialPhenomena.EXPOSURE_OPTION]==PhoneMaterialPhenomena.CURRENT
        val opticsRecipe=if(extended && film["halation_return_model"]=="layer_selective") recipe+("enable_halation" to false) else recipe
        val optics=PhoneExposureOptics(opticsRecipe,width,fullHeight,gain)
        val referencePixels=geometry?.let { it.referenceShortEdgeMm*max(width,fullHeight)/it.longEdgeMm }?.toFloat() ?: min(width,fullHeight).toFloat()
        val layerOptics=if(extended) PhoneLayerExposure(recipe,width,fullHeight,layers,referencePixels,gain) { r,g,b,l ->
            if(layers==1) { if(spectral==null) (r+g+b)/3f else max(spectral.exposure(r,g,b,0),1e-6f) }
            else if(spectral==null) max(0f,r*sensitivity[l][0]+g*sensitivity[l][1]+b*sensitivity[l][2]) else max(spectral.exposure(r,g,b,l),1e-6f)
        } else null
        val response=if(com.filmfoundry.interchange.PhoneProcessModels.interactive(recipe[com.filmfoundry.interchange.PhoneProcessModels.OPTION]) && film["hd_gamma"]!=null) PhoneLayerResponse(film,layers,contrast) else null
        if(response!=null) metadata["phone_layer_response"]="phone_stock_logistic_v1"
        if(optics.enabled) metadata[PhoneExposureOptics.KEY]=optics.record()
        if(layerOptics?.enabled==true) metadata[PhoneLayerExposure.KEY]=layerOptics.record()
        PhoneGrainProfile.fromRecipe(recipe)?.let { metadata[PhoneGrainProfile.KEY]=it }
        if (geometry != null) metadata[DataKernel.MATERIAL_GEOMETRY_KEY] = geometry.metadata()
        if (spectral != null) metadata[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY] = spectral.metadata
        if (materialSpectrum != null) metadata[SpectralOptics.MATERIAL_KEY] = materialSpectrum.metadata
        val setup = ExposureSetup(width, fullHeight, layers, subtractive, half, gain, sensitivity,
            spectral, preparedValues(metadata),response)
        return object: PreparedRowExposure {
            override fun exposeRows(linearRgb: RgbVolume): LatentState {
                if(!optics.enabled && layerOptics?.enabled!=true) return exposePrepared(linearRgb,setup)
                require(linearRgb.width == width && linearRgb.height == fullHeight) { "Spatial exposure needs a full row source; use bindSource" }
                val source=object: LinearRgbRows {
                    override val width=linearRgb.width;override val height=linearRgb.height
                    override fun readRows(start: Int,count: Int)=RgbVolume(width,count,linearRgb=linearRgb.linearRgb.copyOfRange(start*width*3,(start+count)*width*3))
                }
                return bindSource(source).use { it.exposeRows(0,fullHeight) }
            }
            override fun bindSource(source: LinearRgbRows): BoundRowExposure {
                require(source.width == width && source.height == fullHeight)
                if(!optics.enabled && layerOptics?.enabled!=true) return super.bindSource(source)
                val spatial=optics.bind(source)
                val additions=try { layerOptics?.takeIf { it.enabled }?.bind(object: LinearRgbRows {
                    override val width=source.width;override val height=source.height
                    override fun checkActive()=source.checkActive()
                    override fun readRows(start: Int,count: Int)=spatial.preReturnRows(start,count)
                }) } catch(error: Throwable) { spatial.close();throw error }
                return object: BoundRowExposure {
                    private var closed=false
                    override fun exposeRows(start: Int,count: Int): LatentState { check(!closed);source.checkActive();return exposePrepared(spatial.readRows(start,count),setup,start,additions) }
                    override fun close() { closed=true;try { additions?.close() } finally { spatial.close() } }
                }
            }
        }
    }

    private class ExposureSetup(
        val width: Int, val fullHeight: Int, val layers: Int, val subtractive: Boolean,
        val half: Float, val gain: Float, val sensitivity: Array<FloatArray>,
        val spectral: PhoneSpectralExposure?, val metadata: Map<String, Any?>,val response: PhoneLayerResponse?,
    )

    private fun exposePrepared(linearRgb: RgbVolume, setup: ExposureSetup,startRow: Int=0,additions: PhoneLayerExposure.Binding?=null): LatentState {
        require(linearRgb.width == setup.width && linearRgb.height <= setup.fullHeight) { "row dimensions do not match exposure setup" }
        val width = linearRgb.width
        val height = linearRgb.height
        val pixels = width * height
        val layers = setup.layers
        val subtractive = setup.subtractive
        val half = setup.half
        val gain = setup.gain
        val sensitivity = setup.sensitivity
        val spectral = setup.spectral
        val sourceValues=linearRgb.linearRgb
        val response=setup.response

        val halide = FloatArray(pixels * layers) { 1f }
        val silver = FloatArray(pixels * layers)
        val developability = FloatArray(pixels * layers)
        val coupler = if (subtractive) FloatArray(pixels * layers) { 1f } else null
        val dye = if (subtractive) FloatArray(pixels * layers) else null

        for (i in 0 until pixels) {
            val r = max(sourceValues[i * 3] * gain, 0f)
            val g = max(sourceValues[i * 3 + 1] * gain, 0f)
            val b = max(sourceValues[i * 3 + 2] * gain, 0f)
            if (layers == 1) {
                var e = if (spectral == null) (r + g + b) / 3f else
                    max(spectral.exposure(r, g, b, 0), 1e-6f)
                if(additions!=null) e+=additions.addition(i%width,startRow+i/width,0)
                developability[i] = response?.activation(e,0) ?: (e / (e + half))
            } else {
                for (layer in 0 until 3) {
                    val e = if (spectral == null) r * sensitivity[layer][0] + g * sensitivity[layer][1] + b * sensitivity[layer][2]
                        else spectral.exposure(r, g, b, layer)
                    var exposure = max(e, if (spectral == null) 0f else 1e-6f)
                    if(additions!=null) exposure+=additions.addition(i%width,startRow+i/width,layer)
                    developability[i * 3 + layer] = response?.activation(exposure,layer) ?: (exposure / (exposure + half))
                }
            }
        }

        val arrays = linkedMapOf(
            "halide" to FloatNd(intArrayOf(height, width, layers), halide),
            "developability" to FloatNd(intArrayOf(height, width, layers), developability),
            "metallic_silver" to FloatNd(intArrayOf(height, width, layers), silver),
        )
        if (coupler != null) {
            arrays["coupler"] = FloatNd(intArrayOf(height, width, layers), coupler)
            arrays["dye"] = FloatNd(intArrayOf(height, width, layers), dye!!)
        }
        return LatentState(arrays, setup.metadata.toMutableMap())
    }

    override fun develop(
        latent: LatentState,
        recipe: Map<String, Any?>,
        consumeLatent: Boolean,
    ): OpticalMaster {
        return developImpl(latent, recipe, consumeLatent, pool(latent, "halide").shape.firstOrNull() ?: 0)
    }

    override fun developRows(latent: LatentState, recipe: Map<String, Any?>, fullHeight: Int): OpticalMaster {
        require(fullHeight >= pool(latent, "halide").shape[0]) { "fullHeight must contain the row block" }
        return developImpl(latent, recipe, true, fullHeight)
    }

    private fun developImpl(latent: LatentState, recipe: Map<String, Any?>, consumeLatent: Boolean, fullHeight: Int): OpticalMaster {
        val shape = pool(latent, "halide").shape
        if (shape.size != 3 || shape[2] !in listOf(1, 3)) throw KernelException("latent pools must have shape HxWx1 or HxWx3.")
        val setup = developmentSetup(latent, recipe, fullHeight)
        if(setup.spatial?.enabled==true || setup.silverGrain?.enabled==true) {
            require(shape[0]==fullHeight) { "spatial development requires a bound full latent source" }
            val source=object: LatentRows {
                override val width=shape[1];override val height=fullHeight
                override fun readRows(start: Int,count: Int)=LatentState(latent.arrays.mapValues { (_,v)->
                    val row=v.shape[1]*v.shape[2]
                    com.filmfoundry.interchange.FloatNd(intArrayOf(count,v.shape[1],v.shape[2]),v.values.copyOfRange(start*row,(start+count)*row))
                },latent.metadata)
            }
            val fields=setup.spatial?.takeIf { it.enabled }?.bind(source)
            return try {
                val silver=prepareSilverGrain(source,setup,fields)
                developPrepared(latent,setup,consumeLatent,spatial=fields,silverNormalization=silver)
            } finally { fields?.close() }
        }
        return developPrepared(latent, setup, consumeLatent)
    }

    override fun prepareDevelopment(recipe: Map<String, Any?>, firstRows: LatentState, fullHeight: Int): PreparedRowDevelopment {
        val setup = developmentSetup(firstRows, recipe, fullHeight)
        return object: PreparedRowDevelopment {
            override fun developRows(latent: LatentState)=developRowsAt(latent,0)
            override fun developRowsAt(latent: LatentState,startRow: Int): OpticalMaster {
                require(setup.spatial?.enabled!=true && setup.silverGrain?.enabled!=true) { "spatial development requires bindSource" }
                return developPrepared(latent,setup,true,startRow)
            }
            override fun bindSource(source: LatentRows): BoundRowDevelopment {
                require(source.width==setup.width && source.height==setup.fullHeight)
                val fields=setup.spatial?.takeIf { it.enabled }?.bind(source)
                val silver=try { prepareSilverGrain(source,setup,fields) } catch(error: Throwable) { fields?.close();throw error }
                return object: BoundRowDevelopment {
                    override val concurrentRows=true
                    private val grainSamples=ThreadLocal.withInitial { PhoneFormationEffects.GrainSamples() }
                    private var closed=false
                    override fun developRows(start: Int,count: Int): OpticalMaster {
                        check(!closed);source.checkActive()
                        return developPrepared(source.readRows(start,count),setup,false,start,fields,silver,grainSamples.get(),source::checkActive).also { source.checkActive() }
                    }
                    override fun close() { closed=true;grainSamples.remove();fields?.close() }
                }
            }
        }
    }

    private class DevelopmentSetup(
        val width: Int, val fullHeight: Int, val layers: Int, val hasCoupler: Boolean, val hasDye: Boolean,
        val sourceMetadata: Map<String, Any?>, val metadata: Map<String, Any?>,
        val completion: Float, val absorption: Array<FloatArray>, val base: FloatArray,
        val retained: FloatArray, val baseAmounts: DoubleArray?, val cross: Boolean,
        val effects: PhoneFormationEffects,val dyeOpticalScale: FloatArray,
        val program: PhoneProcessProgram?,val spatial: PhoneDevelopmentSpatial?,val silverGrain: PhoneSilverGrain?,
    )

    private fun developmentSetup(latent: LatentState, requestedRecipe: Map<String, Any?>, fullHeight: Int): DevelopmentSetup {
        val recipe=com.filmfoundry.interchange.MaterialSnapshot.resolve(requestedRecipe,latent.metadata)
        val shape = pool(latent, "halide").shape
        if (shape.size != 3 || shape[2] !in listOf(1, 3)) throw KernelException("latent pools must have shape HxWx1 or HxWx3.")
        require(fullHeight >= shape[0]) { "fullHeight must contain the row block" }
        val sourceMetadata = preparedValues(latent.metadata)
        val geometry = if (DataKernel.MATERIAL_GEOMETRY_KEY in sourceMetadata)
            PhoneMaterialGeometry.from(sourceMetadata[DataKernel.MATERIAL_GEOMETRY_KEY], fullHeight, shape[1]) else null
        val spectral = if (DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY in sourceMetadata)
            PhoneSpectralExposure.from(sourceMetadata[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY], shape[2]) else null
        val materialSpectrum = if (SpectralOptics.MATERIAL_KEY in sourceMetadata)
            SpectralOptics.material(sourceMetadata[SpectralOptics.MATERIAL_KEY]) else null
        val baseAmounts = if (materialSpectrum == null) null else SpectralOptics.vector(
            listOf(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0,
                sourceMetadata["masking_coupler_remaining"] ?: 1.0,
                sourceMetadata["auxiliary_remaining"] ?: 1.0), 9)
        val layers = shape[2]
        val hasCoupler = "coupler" in latent.arrays
        val hasDye = "dye" in latent.arrays
        val subtractive = hasCoupler && hasDye && layers == 3
        val develop=mapOf(recipe,"develop").orEmpty()
        val processModel=recipe[com.filmfoundry.interchange.PhoneProcessModels.OPTION]
        val interactive=com.filmfoundry.interchange.PhoneProcessModels.interactive(processModel)
        val material=mapOf(recipe,"film").orEmpty()
        val program=PhoneProcessProgram.from(develop,subtractive,sourceMetadata["image_polarity"]=="positive",material,interactive,
            com.filmfoundry.interchange.PhoneProcessModels.activationLedger(processModel),
            processModel==com.filmfoundry.interchange.PhoneProcessModels.CURRENT)
        val timeMin = numberOf(mapOf(recipe, "develop")?.get("time_min"), 8f).coerceAtLeast(0f)
        val completion = if(develop.keys.any { it in listOf("temperature_c","concentration","agitation","agitation_time_s") }) PhoneProcessProgram.completion(develop)
            else (1.0 - exp((-timeMin / CHARACTERISTIC_TIME).toDouble())).toFloat().coerceIn(0f, 1f)
        val chemistry = mapOf(recipe, "develop")?.get("chemistry")?.toString()?.trim()?.lowercase() ?: "native"
        if (chemistry !in listOf("native", "cross")) throw KernelException("develop.chemistry must be native or cross.")
        val cross = chemistry == "cross" && program==null
        val absorption = matrix3(mapOf(recipe, "film"), "dye_absorption_matrix", DEFAULT_ABSORPTION)
            .map { it.copyOf() }.toTypedArray()
        val dyeOpticalScale=FloatArray(3) { 1f }
        val opticalModel=recipe[com.filmfoundry.interchange.PhoneOpticalDensityModels.OPTION]
        require(opticalModel==null || opticalModel in listOf(com.filmfoundry.interchange.PhoneOpticalDensityModels.CURRENT,com.filmfoundry.interchange.PhoneOpticalDensityModels.PREVIOUS))
        val stockRange=opticalModel==com.filmfoundry.interchange.PhoneOpticalDensityModels.CURRENT
        if(interactive) (material["density_max"] as? List<*>)?.let { values ->
            require(values.size==3)
            for(l in 0..2) { val d=(values[l] as Number).toFloat();require(d.isFinite() && d in .1f..8f)
                // Final dye pools are normalized 0..1. v2 inadvertently halved the declared density range.
                dyeOpticalScale[l]=if(stockRange) d else d/2f;for(c in 0..2) absorption[c][l]*=dyeOpticalScale[l] }
        }
        var base = baseDensity(recipe)
        if(program!=null) {
            if(program.maskRemaining<1f) {
                val support=if(interactive) float3(material["clear_support_density_rgb"],FloatArray(3) { base.minOrNull()!! }) else FloatArray(3) { base.minOrNull()!! }
                base=FloatArray(3) { support[it].coerceIn(0f,base[it])+(base[it]-support[it].coerceIn(0f,base[it]))*program.maskRemaining }
            }
            val auxiliary=float3(if(interactive) material["auxiliary_layer_density_rgb"] ?: material["auxiliary_density_rgb"] else material["auxiliary_density_rgb"],floatArrayOf(0f,0f,0f))
            val amount=if(interactive) numberOf(material["auxiliary_layer_amount"],1f).coerceIn(0f,1f) else 1f
            base=FloatArray(3) { base[it]+auxiliary[it]*amount*program.auxiliaryRemaining }
        }
        val retained = float3(mapOf(recipe, "film")?.get("retained_halide_density_rgb"), DEFAULT_RETAINED)
        val programPositive=program?.positiveFor(subtractive,absorption,retained) ?: false
        val effects=PhoneFormationEffects(program?.effectsRecipe(recipe) ?: recipe,sourceMetadata,shape[1],fullHeight)
        val metadata = developedMetadata(recipe, sourceMetadata, layers, subtractive, cross).toMutableMap()
        metadata["material_binding"]=com.filmfoundry.interchange.MaterialSnapshot.binding(sourceMetadata)
        if(program!=null) {
            metadata["image_polarity"]=if(programPositive) "positive" else "negative"
            metadata["medium_process"]=program.key
            metadata["compatible_interpreters"]=listOf(if(programPositive) "positive_transparency_scan" else "negative_scan")
            val components=program.imageComponents(subtractive)
            metadata["color_system"]=if("dye" in components) "process_dye" else "bw_silver"
            metadata["formed_image_components"]=components
            metadata["formed_image_components_basis"]="unit_layer_response_probe_v1"
            metadata["image_formation_state"]=if(program.hasVariableImage(subtractive)) "image_bearing" else "no_developed_image"
            baseAmounts?.let { it[7]*=program.maskRemaining.toDouble();it[8]*=program.auxiliaryRemaining.toDouble()*(if(interactive) numberOf(material["auxiliary_layer_amount"],1f).coerceIn(0f,1f) else 1f) }
        }
        @Suppress("UNCHECKED_CAST")
        val process = (metadata["film_process_model"] as Map<String, Any?>).toMutableMap()
        if(program!=null) {
            process["ordered_process"]=program.record()
            val implementation=(process["execution_implementation"] as Map<String,Any?>).toMutableMap()
            implementation["component_models"]=(implementation["component_models"] as Map<String,Any?>)+mapOf("develop" to program.model,"optical" to if(interactive) { if(stockRange) com.filmfoundry.interchange.PhoneOpticalDensityModels.CURRENT else com.filmfoundry.interchange.PhoneOpticalDensityModels.PREVIOUS } else "phone_compose_optical_pools_v1")
            process["execution_implementation"]=implementation
        }
        if (geometry != null) process[DataKernel.MATERIAL_GEOMETRY_KEY] = geometry.metadata()
        if (spectral != null) process[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY] = spectral.metadata
        if(effects.enabled) {
            process["phone_material_effects"]=effects.record()
            @Suppress("UNCHECKED_CAST")
            val implementation=(process["execution_implementation"] as Map<String,Any?>).toMutableMap()
            @Suppress("UNCHECKED_CAST")
            val components=(implementation["component_models"] as Map<String,Any?>).toMutableMap()
            if(effects.grain > 0f) components["grain"]=effects.grainModel
            if(effects.grain > 0f && interactive) components["grain_conditioning"]=processModel
            if(effects.hasOverlay) components["post_process"]="piecewise_rgb_density_overlay_v1"
            implementation["component_models"]=components;process["execution_implementation"]=implementation
        }
        (sourceMetadata[PhoneExposureOptics.KEY] as? Map<*,*>)?.let { source ->
            process["exposure_optics"]=source
            @Suppress("UNCHECKED_CAST")
            val implementation=(process["execution_implementation"] as Map<String,Any?>).toMutableMap()
            @Suppress("UNCHECKED_CAST")
            val components=(implementation["component_models"] as Map<String,Any?>).toMutableMap()
            components["mtf"]=source["mtf"];components["halation"]=source["halation"]
            implementation["component_models"]=components;process["execution_implementation"]=implementation
        }
        (sourceMetadata[PhoneLayerExposure.KEY] as? Map<*,*>)?.let { source ->
            process["layer_exposure"]=source
            @Suppress("UNCHECKED_CAST")
            val implementation=(process["execution_implementation"] as Map<String,Any?>).toMutableMap()
            @Suppress("UNCHECKED_CAST")
            val components=(implementation["component_models"] as Map<String,Any?>).toMutableMap()
            components["layer_exposure"]=source["model"];components["light_piping"]=source["light_piping"]
            components["emulsion_highlight_spread"]=source["emulsion_highlight_spread"]
            if(source["halation"]!="omitted_v0") components["halation"]=source["halation"]
            implementation["component_models"]=components;process["execution_implementation"]=implementation
        }
        metadata["film_process_model"] = process
        if (materialSpectrum != null) metadata[SpectralOptics.MEDIUM_KEY] = mapOf(
            "profile" to materialSpectrum.metadata, "base_component_amounts" to baseAmounts!!.toList()) +
            if(effects.hasOverlay) mapOf("post_process_model" to "piecewise_rgb_density_overlay_v1") else emptyMap()
        val spatial=if(processModel==com.filmfoundry.interchange.PhoneProcessModels.CURRENT && program!=null)
            PhoneDevelopmentSpatial(material,develop,sourceMetadata,shape[1],fullHeight,layers,subtractive,program,effects) else null
        if(spatial!=null && spatial.requested && !spatial.enabled) {
            process["spatial_development"]=spatial.inactiveRecord()
            metadata["film_process_model"]=process
        }
        val silver=if(recipe[PhoneMaterialPhenomena.DEVELOPMENT_OPTION]==PhoneMaterialPhenomena.CURRENT)
            PhoneSilverGrain(program?.effectsRecipe(recipe) ?: recipe,sourceMetadata,shape[1],fullHeight) else null
        require(silver?.enabled!=true || program!=null) { "independent silver grain requires an ordered phone process" }
        return DevelopmentSetup(shape[1], fullHeight, layers, hasCoupler, hasDye, sourceMetadata,
            preparedValues(metadata), completion, absorption, base, retained, baseAmounts, cross,effects,dyeOpticalScale,program,spatial,silver)
    }

    /** One read-only full-program pass; scalar normalization, never a full-size silver image. */
    private fun prepareSilverGrain(source: LatentRows,setup: DevelopmentSetup,spatial: PhoneDevelopmentSpatial.Fields?): PhoneSilverGrain.Normalization? {
        val grain=setup.silverGrain?.takeIf { it.enabled } ?: return null
        if(setup.program!!.clearsRetainedSilver) return grain.normalization(0.0,0.0)
        val p=PhoneProcessProgram.Pools();var total=0.0;var candidate=0.0
        val step=source.readRowsPerPass.coerceIn(1,setup.fullHeight)
        for(start in 0 until setup.fullHeight step step) {
            source.checkActive();val count=min(step,setup.fullHeight-start);val block=source.readRows(start,count)
            require(block.metadata==setup.sourceMetadata)
            for((_,array) in block.arrays) require(array.shape.contentEquals(intArrayOf(count,setup.width,setup.layers)))
            val h=pool(block,"halide").values;val a=pool(block,"developability").values;val s=pool(block,"metallic_silver").values
            val c=block.arrays["coupler"]?.values;val d=block.arrays["dye"]?.values;val b=block.arrays["bleached_halide"]?.values
            for(yy in 0 until count) { source.checkActive();val y=start+yy
                for(x in 0 until setup.width) {
                    val first=spatial?.firstRate(x,y) ?: 1f;var mean=0f
                    for(l in 0 until setup.layers) {
                        val i=(yy*setup.width+x)*setup.layers+l
                        p.halide=h[i];p.activation=a[i];p.silver=s[i];p.coupler=c?.get(i) ?: 0f;p.dye=d?.get(i) ?: 0f;p.bleached=b?.get(i) ?: 0f
                        setup.program!!.apply(p,setup.hasCoupler && setup.hasDye && setup.layers==3,setup.effects.completion(1f,x,y),l,first,spatial?.colorRate(x,y,l) ?: 1f)
                        mean+=p.silver
                    }
                    mean/=setup.layers
                    require(mean.isFinite() && mean>=0f) { "invalid retained silver" }
                    total+=mean;candidate+=grain.raw(mean,x,y)
                }
            }
        }
        source.checkActive();return grain.normalization(total,candidate)
    }

    private fun developPrepared(latent: LatentState, setup: DevelopmentSetup, consumeLatent: Boolean,startRow: Int=0,
        spatial: PhoneDevelopmentSpatial.Fields?=null,silverNormalization: PhoneSilverGrain.Normalization?=null,
        grainSamples: PhoneFormationEffects.GrainSamples=PhoneFormationEffects.GrainSamples(),checkActive: ()->Unit={}): OpticalMaster {
        checkActive()
        val shape = pool(latent, "halide").shape
        if (shape.size != 3 || shape[1] != setup.width || shape[0] > setup.fullHeight || shape[2] != setup.layers ||
            ("coupler" in latent.arrays) != setup.hasCoupler || ("dye" in latent.arrays) != setup.hasDye ||
            latent.metadata != setup.sourceMetadata
        ) throw KernelException("latent rows do not match development setup.")
        require(startRow >= 0 && startRow+shape[0] <= setup.fullHeight)
        val baseAmounts = setup.baseAmounts
        if (baseAmounts != null) {
            latent.arrays["bleached_halide"]?.let {
                if (!it.shape.contentEquals(shape) || it.values.any { v -> !v.isFinite() || v < 0f })
                    throw KernelException("spectral bleached_halide must match finite nonnegative material pools.")
            }
        }
        val halide = pool(latent, "halide")
        val developability = pool(latent, "developability")
        val silver = pool(latent, "metallic_silver")
        val coupler = latent.arrays["coupler"]
        val dye = latent.arrays["dye"]
        val bleached = latent.arrays["bleached_halide"]?.values
        if (halide.shape.contentEquals(developability.shape).not() ||
            halide.shape.contentEquals(silver.shape).not()
            || (coupler != null && !coupler.shape.contentEquals(shape))
            || (dye != null && !dye.shape.contentEquals(shape))
        ) {
            throw KernelException("latent pools must share shape HxWxL.")
        }
        val layers = halide.shape[2]
        val subtractive = coupler != null && dye != null && layers == 3
        val completion = setup.completion

        val pixels = shape[0] * shape[1]
        val absorption = setup.absorption
        val base = setup.base
        val retained = setup.retained
        // Task-local references preserve array aliasing and mutation order while avoiding
        // repeated property access in the pixel loop (also on unoptimized Android code).
        val halideValues=halide.values;val activationValues=developability.values
        val silverValues=silver.values;val couplerValues=coupler?.values;val dyeValues=dye?.values
        val width=setup.width;val program=setup.program;val effects=setup.effects
        val cross=setup.cross;val dyeOpticalScale=setup.dyeOpticalScale
        // Keep the existing consuming mutation order, including aliased caller pools.
        if (consumeLatent && program==null) for (i in halideValues.indices) {
            val pixel=i/layers
            val localCompletion=effects.completion(completion,pixel%width,startRow+pixel/width)
            val fraction = activationValues[i].coerceIn(0f, 1f) * localCompletion
            val consumed = halideValues[i] * fraction
            if (cross && subtractive) {
                val remaining = (halideValues[i] - consumed).coerceAtLeast(0f)
                halideValues[i] = 0f
                val coupled = min(couplerValues!![i], remaining)
                coupler.values[i] = (coupler.values[i] - coupled).coerceAtLeast(0f)
                dyeValues!![i] += coupled
                silverValues[i] = 0f
            } else if (cross) {
                halideValues[i] = (halideValues[i] - consumed).coerceAtLeast(0f)
                activationValues[i] = consumed
                silverValues[i] = 0f
            } else {
                halideValues[i] = (halideValues[i] - consumed).coerceAtLeast(0f)
                if (subtractive) {
                    val coupled = min(couplerValues!![i], consumed)
                    coupler.values[i] = (coupler.values[i] - coupled).coerceAtLeast(0f)
                    dyeValues!![i] += coupled
                    silverValues[i] = 0f
                } else silverValues[i] += consumed
            }
        }
        val opticalValues = FloatArray(pixels * 3)
        val amountValues = if (baseAmounts == null) null else FloatArray(pixels * 9)
        val overlayValues=if(baseAmounts != null && effects.hasOverlay) FloatArray(pixels*3) else null
        // The phone process is pointwise. Carry only one pixel's final pools into
        // optical composition, rather than copying four full-frame mutable pools.
        val finalHalide = FloatArray(layers)
        val finalSilver = FloatArray(layers)
        val finalDye = FloatArray(layers)
        val finalBleached = FloatArray(layers)
        val pools=PhoneProcessProgram.Pools()
        for (pixel in 0 until pixels) {
            if(pixel and 255==0) checkActive()
            val x=pixel%width;val y=startRow+pixel/width
            val firstRate=spatial?.firstRate(x,y) ?: 1f
            val localCompletion=effects.completion(if(program!=null) 1f else completion,x,y)
            var silverMean = 0f
            var halideMean = 0f
            for (layer in 0 until layers) {
                val i = pixel * layers + layer
                if(program!=null) {
                    pools.halide=halideValues[i];pools.activation=activationValues[i];pools.silver=silverValues[i]
                    pools.coupler=couplerValues?.get(i) ?: 0f;pools.dye=dyeValues?.get(i) ?: 0f
                    pools.bleached=bleached?.get(i) ?: 0f
                    program.apply(pools,subtractive,localCompletion,layer,
                        firstRate,spatial?.colorRate(x,y,layer) ?: 1f)
                    finalHalide[layer]=pools.halide;finalSilver[layer]=pools.silver;finalDye[layer]=pools.dye;finalBleached[layer]=pools.bleached
                    if(consumeLatent) {
                        halideValues[i]=pools.halide;activationValues[i]=pools.activation;silverValues[i]=pools.silver
                        couplerValues?.set(i,pools.coupler);dyeValues?.set(i,pools.dye);bleached?.set(i,pools.bleached)
                    }
                } else if (consumeLatent) {
                    finalHalide[layer] = halideValues[i]
                    finalSilver[layer] = silverValues[i]
                    if (subtractive) finalDye[layer] = dyeValues!![i]
                    else if (cross) finalDye[layer] = activationValues[i]
                } else {
                    val consumed = halideValues[i] * activationValues[i].coerceIn(0f, 1f) * localCompletion
                    if (cross && subtractive) {
                        val remaining = (halideValues[i] - consumed).coerceAtLeast(0f)
                        finalHalide[layer] = 0f
                        finalDye[layer] = dyeValues!![i] + min(couplerValues!![i], remaining)
                        finalSilver[layer] = 0f
                    } else if (cross) {
                        finalHalide[layer] = (halideValues[i] - consumed).coerceAtLeast(0f)
                        finalDye[layer] = consumed
                        finalSilver[layer] = 0f
                    } else {
                        finalHalide[layer] = (halideValues[i] - consumed).coerceAtLeast(0f)
                        if (subtractive) {
                            val coupled = min(couplerValues!![i], consumed)
                            finalDye[layer] = dyeValues!![i] + coupled
                            finalSilver[layer] = 0f
                        } else finalSilver[layer] = silverValues[i] + consumed
                    }
                }
                // Imaging amounts vary; chemical ledger and support/base remain separate.
                val factor=if(silverNormalization!=null && finalDye[layer]==0f) 1f else
                    effects.grainFactor(x,y,layer,if(subtractive || cross) finalDye[layer] else finalSilver[layer],grainSamples)
                finalDye[layer]*=factor
                if(silverNormalization==null) finalSilver[layer]*=factor
                silverMean += finalSilver[layer]
                halideMean += finalHalide[layer]
            }
            silverMean /= layers
            halideMean /= layers
            if(silverNormalization!=null && silverMean>0f) {
                val varied=setup.silverGrain!!.normalized(silverMean,x,y,silverNormalization)
                val ratio=varied/silverMean;for(l in 0 until layers) finalSilver[l]*=ratio
                silverMean=varied
            }
            val bleachedDensity=if(program!=null) finalBleached.sum()/layers*.12f else 0f
            for (c in 0 until 3) {
                var density = base[c]
                density += silverMean * SILVER_DENSITY
                density += halideMean * 0.12f * retained[c]
                if(program!=null) density+=bleachedDensity*retained[c]
                if (subtractive) {
                    var dyeDensity = 0f
                    for (layer in 0 until 3) dyeDensity += absorption[c][layer] * finalDye[layer]
                    density += dyeDensity
                } else if (cross) density += absorption[c][0] * finalDye[0]
                val overlay=effects.overlay(x,y,c)
                overlayValues?.set(pixel*3+c,overlay)
                opticalValues[pixel * 3 + c] = max(density+overlay, 0f)
            }
            if (amountValues != null) {
                for (layer in 0 until layers) {
                    if (subtractive) amountValues[pixel*9+layer] = finalDye[layer]*dyeOpticalScale[layer]
                    else if (cross && layer == 0) amountValues[pixel*9] = finalDye[0]
                    amountValues[pixel*9+3] += finalSilver[layer] / layers
                    amountValues[pixel*9+4] += finalHalide[layer] / layers
                    amountValues[pixel*9+5] += (if(program!=null) finalBleached[layer] else bleached?.get(pixel*layers+layer) ?: 0f) / layers
                }
                for (k in 6..8) amountValues[pixel*9+k] = baseAmounts!![k].toFloat()
            }
        }
        val optical = FloatNd(intArrayOf(shape[0], shape[1], 3), opticalValues)
        val metadata = setup.metadata.toMutableMap()
        if(spatial!=null) {
            @Suppress("UNCHECKED_CAST")
            val process=(metadata["film_process_model"] as Map<String,Any?>).toMutableMap()
            process["spatial_development"]=spatial.record
            @Suppress("UNCHECKED_CAST")
            val implementation=(process["execution_implementation"] as Map<String,Any?>).toMutableMap()
            @Suppress("UNCHECKED_CAST")
            val components=(implementation["component_models"] as Map<String,Any?>).toMutableMap()
            components["spatial_development"]=PhoneDevelopmentSpatial.MODEL
            implementation["component_models"]=components;process["execution_implementation"]=implementation
            metadata["film_process_model"]=process
        }
        if(silverNormalization!=null) {
            @Suppress("UNCHECKED_CAST")
            val process=(metadata["film_process_model"] as Map<String,Any?>).toMutableMap()
            process["independent_silver_grain"]=silverNormalization.record
            @Suppress("UNCHECKED_CAST")
            val implementation=(process["execution_implementation"] as Map<String,Any?>).toMutableMap()
            @Suppress("UNCHECKED_CAST")
            val components=(implementation["component_models"] as Map<String,Any?>).toMutableMap()
            components["silver_grain"]=PhoneSilverGrain.MODEL
            implementation["component_models"]=components;process["execution_implementation"]=implementation
            metadata["film_process_model"]=process
        }
        val amounts = amountValues?.let { FloatNd(intArrayOf(shape[0], shape[1], 9), it) }
        return OpticalMaster(
            opticalDensityRgb = optical,
            clearBaseOpticalDensityRgb = base.copyOf(),
            metadata = metadata,
            spectralComponentAmounts = amounts,
            spectralPostProcessDensityRgb = overlayValues?.let { FloatNd(intArrayOf(shape[0],shape[1],3),it) },
        )
    }

    override fun observe(optical: OpticalMaster, recipe: Map<String, Any?>): ScanObservation {
        return PhoneObserve.run(optical, recipe, baseDensity(recipe))
    }

    private fun latentMetadata(
        recipe: Map<String, Any?>,
        layers: Int,
        subtractive: Boolean,
    ): Map<String, Any?> {
        val payload = identityFromRecipe(recipe, subtractive).toMutableMap()
        payload[DataKernel.LATENT_KIND_KEY] = DataKernel.LATENT_KIND
        payload[DataKernel.LATENT_STAGE_KEY] = DataKernel.LATENT_STAGE
        payload["layer_count"] = layers
        payload["auxiliary_remaining"] = 1.0
        payload["masking_coupler_remaining"] = 1.0
        payload["consumable"] = true
        payload["consumed"] = false
        copyFrameIdentity(recipe, payload)
        return payload
    }

    private fun developedMetadata(
        recipe: Map<String, Any?>,
        latentMeta: Map<String, Any?>,
        layers: Int,
        subtractive: Boolean,
        cross: Boolean,
    ): Map<String, Any?> {
        val payload = identityFromRecipe(recipe, subtractive).toMutableMap()
        if (cross && subtractive) {
            payload["image_polarity"] = "positive"
            payload["medium_process"] = "cross_positive"
            payload["color_system"] = "cross_reversal_dye"
            payload["compatible_interpreters"] = listOf("positive_transparency_scan")
        } else if (cross) {
            payload["medium_process"] = "cross_negative"
            payload["color_system"] = "cross_bw_dye"
        }
        for (key in listOf(DataKernel.FRAME_IDENTITY_KEY)) {
            if (key in latentMeta) payload[key] = latentMeta[key]
        }
        copyFrameIdentity(recipe, payload)
        payload["film_process_model"] = mapOf(
            "execution_implementation" to mapOf(
                "profile_id" to DataKernel.PHONE_REDUCED_PROFILE_ID,
                "lower_order_enabled" to true,
                "component_models" to (mapOf(
                    "expose" to (latentMeta["phone_layer_response"] ?: "phone_half_saturation_latent_v0"),
                    "develop" to if (cross) DataKernel.PHONE_CROSS_CHEMISTRY_ID else "phone_first_order_develop_v0",
                    "optical" to "phone_compose_optical_v0",
                    "observe" to DataKernel.PHONE_TRANSMISSION_CAPTURE_ID,
                    "mtf" to "omitted_v0",
                    "halation" to "omitted_v0",
                    "grain" to "omitted_v0",
                ) + if (DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY in latentMeta)
                    mapOf("exposure_mapping" to (latentMeta[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY] as Map<*, *>)["model"]) else emptyMap()),
            ),
        )
        payload["authoritative_optical_density_rgb"] = true
        payload["layer_count"] = layers
        return payload
    }

    private fun identityFromRecipe(recipe: Map<String, Any?>, subtractive: Boolean): MutableMap<String, Any?> {
        val polarity = polarityOf(recipe, recipe)
        val positive = polarity == "positive"
        return mutableMapOf(
            "medium_family" to "film",
            "medium_process" to if (positive) "slide" else if (subtractive) "negative" else "bw_negative",
            "image_polarity" to polarity,
            "view_mode" to "transmissive",
            "base_type" to if (positive || !subtractive) "clear_base" else "orange_mask",
            "color_system" to if (positive) "positive_dye" else if (subtractive) "color_negative_dye" else "bw_silver",
            "compatible_interpreters" to listOf(
                if (positive) "positive_transparency_scan" else "negative_scan",
            ),
        )
    }

    private fun polarityOf(recipe: Map<String, Any?>, metadata: Map<String, Any?>): String {
        val explicit = metadata["image_polarity"] ?: mapOf(recipe, "film")?.get("image_polarity")
            ?: recipe["medium"]
        val text = explicit?.toString()?.trim()?.lowercase().orEmpty()
        return when {
            "positive" in text || text == "slide" -> "positive"
            else -> "negative"
        }
    }

    private fun copyFrameIdentity(recipe: Map<String, Any?>, payload: MutableMap<String, Any?>) {
        val identity = recipe[DataKernel.FRAME_IDENTITY_KEY] as? Map<*, *> ?: return
        payload[DataKernel.FRAME_IDENTITY_KEY] = identity.entries.associate { it.key.toString() to it.value }
    }

    private fun pool(latent: LatentState, key: String): FloatNd {
        return latent.arrays[key] ?: throw KernelException("latent state missing $key.")
    }

    private fun mapOf(recipe: Map<String, Any?>, key: String): Map<String, Any?>? {
        val raw = recipe[key] as? Map<*, *> ?: return null
        return raw.entries.associate { it.key.toString() to it.value }
    }

    private fun numberOf(value: Any?, default: Float): Float {
        return when (value) {
            null -> default
            is Number -> value.toFloat()
            else -> default
        }
    }

    private fun booleanOf(value: Any?, default: Boolean): Boolean {
        return when (value) {
            null -> default
            is Boolean -> value
            else -> default
        }
    }

    internal fun baseDensity(recipe: Map<String, Any?>): FloatArray {
        return float3(mapOf(recipe, "film")?.get("film_base_density_rgb"), DEFAULT_BASE)
    }

    private fun float3(value: Any?, default: FloatArray): FloatArray {
        val list = value as? List<*> ?: return default.copyOf()
        if (list.size != 3) return default.copyOf()
        return FloatArray(3) { idx ->
            val item = list[idx]
            if (item is Number) max(item.toFloat(), 0f) else default[idx]
        }
    }

    private fun matrix3(
        film: Map<String, Any?>?,
        key: String,
        default: Array<FloatArray>,
    ): Array<FloatArray> {
        val raw = film?.get(key) as? List<*> ?: return default
        if (raw.size != 3) return default
        return Array(3) { row ->
            val line = raw[row] as? List<*> ?: return default
            if (line.size != 3) return default
            FloatArray(3) { col ->
                val item = line[col]
                if (item is Number) item.toFloat() else default[row][col]
            }
        }
    }

    companion object {
        const val HALF_SATURATION = 0.18f
        const val CHARACTERISTIC_TIME = 8f
        const val SILVER_DENSITY = 0.85f
        const val OBSERVE_GAIN = 0.18f
        val DEFAULT_SENSITIVITY = arrayOf(
            floatArrayOf(0.82f, 0.14f, 0.04f),
            floatArrayOf(0.10f, 0.78f, 0.12f),
            floatArrayOf(0.05f, 0.20f, 0.75f),
        )
        val DEFAULT_ABSORPTION = arrayOf(
            floatArrayOf(1.00f, 0.10f, 0.04f),
            floatArrayOf(0.08f, 1.00f, 0.12f),
            floatArrayOf(0.03f, 0.16f, 1.00f),
        )
        val DEFAULT_BASE = floatArrayOf(0.18f, 0.55f, 0.85f)
        val DEFAULT_RETAINED = floatArrayOf(0.62f, 0.82f, 1.0f)
    }
}
