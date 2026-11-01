package com.filmfoundry.platform

import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.GLES31 as GL
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Optional offscreen GLES 3.1 row compute. One worker owns context/buffers; camera rendering is untouched. */
class GlesSrgbRowCompute: SrgbRowCompute {
    private val owner=Thread.currentThread()
    private var display=EGL14.EGL_NO_DISPLAY
    private var context=EGL14.EGL_NO_CONTEXT
    private var surface=EGL14.EGL_NO_SURFACE
    private var program=0
    private val buffers=IntArray(4)
    private var upload: ByteBuffer=ByteBuffer.allocateDirect(0)
    private val bufferBytes=IntArray(4)
    private var coefficients: com.filmfoundry.kernel.SpectralTransmissionPlan?=null
    private var outputCoefficients: com.filmfoundry.kernel.ScanOutputPlan?=null
    private var sizeUniform=-1
    private var operationUniform=-1
    private var waveUniform=-1
    private var postUniform=-1
    init {
        try {
            display=EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
            check(display!=EGL14.EGL_NO_DISPLAY && EGL14.eglInitialize(display,IntArray(2),0,IntArray(2),0)) { "EGL unavailable" }
            val configs=arrayOfNulls<EGLConfig>(1);val count=IntArray(1)
            check(EGL14.eglChooseConfig(display,intArrayOf(EGL14.EGL_RENDERABLE_TYPE,0x40,EGL14.EGL_SURFACE_TYPE,EGL14.EGL_PBUFFER_BIT,EGL14.EGL_NONE),0,configs,0,1,count,0) && count[0]>0) { "GLES 3 config unavailable" }
            context=EGL14.eglCreateContext(display,configs[0],EGL14.EGL_NO_CONTEXT,intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION,3,EGL14.EGL_NONE),0)
            surface=EGL14.eglCreatePbufferSurface(display,configs[0],intArrayOf(EGL14.EGL_WIDTH,1,EGL14.EGL_HEIGHT,1,EGL14.EGL_NONE),0)
            check(context!=EGL14.EGL_NO_CONTEXT && surface!=EGL14.EGL_NO_SURFACE) { "Offscreen compute unavailable" }
            current {
                val shader=GL.glCreateShader(GL.GL_COMPUTE_SHADER)
                try {
                    GL.glShaderSource(shader,SOURCE);GL.glCompileShader(shader)
                    val status=IntArray(1);GL.glGetShaderiv(shader,GL.GL_COMPILE_STATUS,status,0)
                    check(status[0]!=0) { "GLES 3.1 shader unavailable: ${GL.glGetShaderInfoLog(shader)}" }
                    program=GL.glCreateProgram();GL.glAttachShader(program,shader);GL.glLinkProgram(program)
                    GL.glGetProgramiv(program,GL.GL_LINK_STATUS,status,0);check(status[0]!=0) { "Compute link failed" }
                    sizeUniform=GL.glGetUniformLocation(program,"size");operationUniform=GL.glGetUniformLocation(program,"operation")
                    waveUniform=GL.glGetUniformLocation(program,"waves");postUniform=GL.glGetUniformLocation(program,"hasPost")
                    GL.glGenBuffers(4,buffers,0);checkGl()
                } finally { GL.glDeleteShader(shader) }
            }
        } catch(error: Exception) { close();throw error }
    }
    private fun <T> current(work: ()->T): T {
        check(Thread.currentThread()===owner) { "GPU context used by a different worker" }
        val previousDisplay=EGL14.eglGetCurrentDisplay();val previousContext=EGL14.eglGetCurrentContext()
        val draw=EGL14.eglGetCurrentSurface(EGL14.EGL_DRAW);val read=EGL14.eglGetCurrentSurface(EGL14.EGL_READ)
        check(EGL14.eglMakeCurrent(display,surface,surface,context)) { "Cannot bind compute context" }
        return try { work() } finally {
            if(previousDisplay!=EGL14.EGL_NO_DISPLAY) EGL14.eglMakeCurrent(previousDisplay,draw,read,previousContext)
            else EGL14.eglMakeCurrent(display,EGL14.EGL_NO_SURFACE,EGL14.EGL_NO_SURFACE,EGL14.EGL_NO_CONTEXT)
        }
    }
    override fun linearize(encoded: FloatArray)=execute(encoded,0)
    override fun transmit(density: FloatArray)=execute(density,1)
    override fun transmitSpectral(amounts: FloatArray,post: FloatArray?,plan: com.filmfoundry.kernel.SpectralTransmissionPlan): FloatArray =
        execute(amounts,2,post,plan)
    override fun encodeOutput(linear: FloatArray,plan: com.filmfoundry.kernel.ScanOutputPlan): FloatArray =
        execute(linear,3,outputPlan=plan)
    private fun outputParameters(plan: com.filmfoundry.kernel.ScanOutputPlan): FloatArray {
        val n=plan.normalization;val e=plan.encoding
        val parameters=MutableList(24) { 0f }
        parameters[0]=if(n==null) 0f else if(n.rgb) 2f else 1f
        parameters[1]=if(plan.boundedInput) 1f else 0f;parameters[2]=n?.strength ?: 0f
        for(c in 0..2) {
            parameters[3+c]=n?.black?.get(if(n.rgb) c else 0) ?: 0f
            parameters[6+c]=n?.spans?.get(if(n.rgb) c else 0) ?: 0f
            parameters[10+c]=e.lampColor?.get(c) ?: 1f
        }
        parameters[9]=e.printGain;parameters[13]=e.contrast
        fun curve(slot: Int,points: List<Pair<Float,Float>>?) {
            if(points==null) return
            require(points.size<=256) { "Output curve exceeds GPU bound" }
            parameters[slot]=parameters.size.toFloat();parameters[slot+1]=points.size.toFloat()
            for((x,y) in points) { parameters+=x;parameters+=y }
        }
        for(c in 0..2) curve(14+c*2,e.curves?.get(c))
        curve(20,e.tone?.takeIf { points -> points.any { kotlin.math.abs(it.first-it.second)>1e-7f } })
        require(parameters.all { it>=-Float.MAX_VALUE && it<=Float.MAX_VALUE }) { "Output parameters exceed GPU range" }
        return parameters.toFloatArray()
    }
    private fun allocate(index: Int,bytes: Int) {
        GL.glBindBuffer(GL.GL_SHADER_STORAGE_BUFFER,buffers[index])
        if(bytes>bufferBytes[index]) {
            GL.glBufferData(GL.GL_SHADER_STORAGE_BUFFER,bytes,null,if(index==1) GL.GL_DYNAMIC_READ else GL.GL_DYNAMIC_DRAW)
            bufferBytes[index]=bytes
        }
        GL.glBindBufferBase(GL.GL_SHADER_STORAGE_BUFFER,index,buffers[index])
    }
    private fun upload(index: Int,values: FloatArray) {
        val bytes=Math.multiplyExact(values.size,4)
        allocate(index,maxOf(4,bytes))
        if(bytes==0) return
        if(upload.capacity()<bytes) upload=ByteBuffer.allocateDirect(bytes).order(ByteOrder.nativeOrder())
        upload.clear();upload.asFloatBuffer().put(values)
        GL.glBufferSubData(GL.GL_SHADER_STORAGE_BUFFER,0,bytes,upload)
    }
    private fun execute(encoded: FloatArray,operation: Int,post: FloatArray?=null,plan: com.filmfoundry.kernel.SpectralTransmissionPlan?=null,
        outputPlan: com.filmfoundry.kernel.ScanOutputPlan?=null): FloatArray = current {
        require(encoded.size<=DeferredPixelCompute.MAX_GPU_COMPONENTS) { "GPU row buffer exceeds bound" }
        if(encoded.isEmpty()) return@current FloatArray(0)
        val count=if(operation==2) encoded.size/9 else if(operation==3) encoded.size/3 else encoded.size
        val outputCount=if(operation==2 || operation==3) count*3 else count
        val bytes=Math.multiplyExact(outputCount,4)
        if(operation==2) {
            require(encoded.size%9==0 && count<=DeferredPixelCompute.MAX_SPECTRAL_PIXELS && (post==null || post.size==count*3))
            val config=requireNotNull(plan)
            require(config.wavelengths<=256) { "GPU spectral grid exceeds bound" }
            val previous=coefficients
            if(previous==null || previous.wavelengths!=config.wavelengths || !previous.absorbance.contentEquals(config.absorbance) ||
                !previous.weights.contentEquals(config.weights) || !previous.overlay.contentEquals(config.overlay)) {
                upload(2,FloatArray(config.wavelengths*15) { index ->
                    val n=index/15;val k=index%15
                    (if(k<9) config.absorbance[n*9+k] else if(k<12) config.weights[n*3+k-9] else config.overlay[n*3+k-12]).toFloat()
                })
                coefficients=config
                outputCoefficients=null
            } else GL.glBindBufferBase(GL.GL_SHADER_STORAGE_BUFFER,2,buffers[2])
            upload(3,post ?: floatArrayOf(0f))
        } else if(operation==3) {
            require(encoded.size%3==0)
            val config=requireNotNull(outputPlan)
            if(outputCoefficients!==config) {
                upload(2,outputParameters(config));outputCoefficients=config;coefficients=null
            } else GL.glBindBufferBase(GL.GL_SHADER_STORAGE_BUFFER,2,buffers[2])
            allocate(3,4)
        } else {
            // All four SSBOs are active in this shared program, even when this operation
            // does not read spectral coefficients/overlay. Bind valid buffers on every
            // dispatch; keep any cached coefficients intact for the next spectral call.
            allocate(2,4);allocate(3,4)
        }
        upload(0,encoded);allocate(1,bytes)
        GL.glUseProgram(program);GL.glUniform1i(sizeUniform,count);GL.glUniform1i(operationUniform,operation)
        GL.glUniform1i(waveUniform,plan?.wavelengths ?: 0);GL.glUniform1i(postUniform,if(post!=null) 1 else 0)
        GL.glDispatchCompute((count+127)/128,1,1);GL.glMemoryBarrier(GL.GL_BUFFER_UPDATE_BARRIER_BIT or GL.GL_SHADER_STORAGE_BARRIER_BIT)
        checkGl()
        val fence=GL.glFenceSync(GL.GL_SYNC_GPU_COMMANDS_COMPLETE,0)
        try {
            val deadline=System.nanoTime()+5_000_000_000L
            while(true) {
                val result=GL.glClientWaitSync(fence,GL.GL_SYNC_FLUSH_COMMANDS_BIT,50_000_000L)
                if(result==GL.GL_ALREADY_SIGNALED || result==GL.GL_CONDITION_SATISFIED) break
                check(result!=GL.GL_WAIT_FAILED && System.nanoTime()<deadline) { "Compute completion timed out" }
            }
        } finally { GL.glDeleteSync(fence) }
        GL.glBindBuffer(GL.GL_SHADER_STORAGE_BUFFER,buffers[1])
        val mapped=GL.glMapBufferRange(GL.GL_SHADER_STORAGE_BUFFER,0,bytes,GL.GL_MAP_READ_BIT) as? ByteBuffer ?: error("Compute readback unavailable")
        try { FloatArray(outputCount).also { mapped.order(ByteOrder.nativeOrder()).asFloatBuffer().get(it) } }
        finally { check(GL.glUnmapBuffer(GL.GL_SHADER_STORAGE_BUFFER));checkGl() }
    }
    private fun checkGl() { check(GL.glGetError()==GL.GL_NO_ERROR) { "GLES compute error" } }
    override fun close() {
        check(Thread.currentThread()===owner)
        try { if(context!=EGL14.EGL_NO_CONTEXT && surface!=EGL14.EGL_NO_SURFACE) current {
            if(program!=0) GL.glDeleteProgram(program);program=0
            GL.glDeleteBuffers(4,buffers,0);buffers.fill(0)
        } } finally {
            if(surface!=EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(display,surface)
            if(context!=EGL14.EGL_NO_CONTEXT) EGL14.eglDestroyContext(display,context)
            if(display!=EGL14.EGL_NO_DISPLAY) EGL14.eglTerminate(display)
            surface=EGL14.EGL_NO_SURFACE;context=EGL14.EGL_NO_CONTEXT;display=EGL14.EGL_NO_DISPLAY
        }
    }
    companion object {
        private const val SOURCE="""#version 310 es
precision highp float;
layout(local_size_x=128) in;
layout(std430,binding=0) readonly buffer Source { float encoded[]; };
layout(std430,binding=1) writeonly buffer Result { float linear[]; };
uniform int size;
layout(std430,binding=2) readonly buffer Coefficients { float coefficients[]; };
layout(std430,binding=3) readonly buffer Overlay { float post[]; };
uniform int operation;
uniform int waves;
uniform int hasPost;
float boundNegative(float x) { x=max(x,0.0);return x/(1.0+x); }
float outputCurve(float value,int slot) {
    int start=int(coefficients[slot]);int count=int(coefficients[slot+1]);
    if(count==0) return value;
    if(value<=coefficients[start]) return coefficients[start+1];
    int last=start+(count-1)*2;
    if(value>=coefficients[last]) return coefficients[last+1];
    for(int p=0;p<count-1;p++) {
        int left=start+p*2;int right=left+2;
        if(value<=coefficients[right]) {
            float span=coefficients[right]-coefficients[left];
            float t=span<=0.000001 ? 0.0 : (value-coefficients[left])/span;
            return coefficients[left+1]+t*(coefficients[right+1]-coefficients[left+1]);
        }
    }
    return value;
}
vec3 outputChain(vec3 src) {
    int mode=int(coefficients[0]);bool bounded=coefficients[1]>0.5;
    float strength=coefficients[2];vec3 normalized=src;
    if(mode==2) {
        for(int c=0;c<3;c++) {
            float span=coefficients[6+c];float base=bounded ? boundNegative(src[c]) : src[c];
            normalized[c]=span<=0.000001 ? base : base*(1.0-strength)+clamp((src[c]-coefficients[3+c])/span,0.0,1.0)*strength;
        }
    } else if(mode==1 && coefficients[6]>0.000001) {
        float y=0.2126*src.r+0.7152*src.g+0.0722*src.b;
        float stretched=clamp((y-coefficients[3])/coefficients[6],0.0,1.0);
        float gain=y>0.000001 ? (bounded ? stretched : y*(1.0-strength)+stretched*strength)/y : 0.0;
        for(int c=0;c<3;c++) {
            float target=max(src[c]*gain,0.0);
            normalized[c]=bounded ? boundNegative(src[c])*(1.0-strength)+target*strength : target;
        }
    } else if(bounded) {
        for(int c=0;c<3;c++) normalized[c]=boundNegative(src[c]);
    }
    vec3 encoded;
    for(int c=0;c<3;c++) {
        float value=(coefficients[9]!=1.0 ? normalized[c]*coefficients[9] : normalized[c])*coefficients[10+c];
        if(coefficients[13]!=1.0) value=(value-0.5)*coefficients[13]+0.5;
        value=clamp(value,0.0,1.0);
        value=value<=0.0031308 ? 12.92*value : 1.055*pow(value,1.0/2.4)-0.055;
        encoded[c]=clamp(outputCurve(value,14+c*2),0.0,1.0);
    }
    if(coefficients[21]>0.0) {
        float brightness=encoded.r*0.2126+encoded.g*0.7152+encoded.b*0.0722;
        float target=outputCurve(brightness,20);
        for(int c=0;c<3;c++) encoded[c]=clamp(brightness<=0.00000001 ? target : encoded[c]*(target/brightness),0.0,1.0);
    }
    return encoded;
}
void main() {
    uint i=gl_GlobalInvocationID.x;if(i>=uint(size)) return;
    if(operation==3) {
        vec3 result=outputChain(vec3(encoded[i*3u],encoded[i*3u+1u],encoded[i*3u+2u]));
        linear[i*3u]=result.r;linear[i*3u+1u]=result.g;linear[i*3u+2u]=result.b;return;
    }
    if(operation==2) {
        vec3 sum=vec3(0.0);
        for(int n=0;n<waves;n++) {
            int a=n*15;float density=0.0;
            for(int k=0;k<9;k++) density+=encoded[i*9u+uint(k)]*coefficients[a+k];
            if(hasPost==1) {
                for(int k=0;k<3;k++) density+=post[i*3u+uint(k)]*coefficients[a+12+k];
                density=max(density,0.0);
            }
            float t=pow(10.0,-min(density,80.0));
            sum+=t*vec3(coefficients[a+9],coefficients[a+10],coefficients[a+11]);
        }
        linear[i*3u]=sum.r;linear[i*3u+1u]=sum.g;linear[i*3u+2u]=sum.b;return;
    }
    if(operation==1) { linear[i]=max(pow(10.0,-max(encoded[i],0.0)),0.000001);return; }
    float u=clamp(encoded[i],0.0,1.0);
    linear[i]=u<=0.04045 ? u/12.92 : pow((u+0.055)/1.055,2.4);
}
"""
    }
}
