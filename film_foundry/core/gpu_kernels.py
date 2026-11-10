"""Shared CUDA/OpenCL arithmetic; backend headers only change launch syntax.

No fast math: spectral density and integration keep the CPU's float64 precision.
The nine component amounts and optional signed overlay remain float32 storage.
"""

DYE = r'''
KERNEL void dye(GLOBAL const float* src, GLOBAL const float* a, GLOBAL const float* base,
 GLOBAL const float* overlap, GLOBAL const float* weighted, GLOBAL const float* response,
 float strength, int legacy, int count, GLOBAL float* dst) {
 int p=PIXEL;
 if(p>=count) return;
 float t[3];
 for(int c=0;c<3;c++) {
   float d=src[p*3]*a[c*3]+src[p*3+1]*a[c*3+1]+src[p*3+2]*a[c*3+2];
   if(legacy) dst[p*3+c]=fmaxf(d+base[c],0.f);
   else t[c]=powf(10.f,-fmaxf(d,0.f));
 }
 if(legacy) return;
 for(int c=0;c<3;c++) {
   float r=t[0]*overlap[c*3]+t[1]*overlap[c*3+1]+t[2]*overlap[c*3+2];
   float v=t[0]*weighted[c*3]+t[1]*weighted[c*3+1]+t[2]*weighted[c*3+2];
   v=(v/response[c])/fmaxf(r,1.e-6f);
   v=powf(fminf(fmaxf(v,1.e-4f),1.e4f),strength)*t[c];
   dst[p*3+c]=fmaxf(-log10f(fminf(fmaxf(v,1.e-6f),1.f))+base[c],0.f);
 }
}
'''

SPECTRAL = r'''
KERNEL void spectral(GLOBAL const float* src, GLOBAL const double* absorbance,
 GLOBAL const double* weights, GLOBAL const float* overlay, GLOBAL const double* basis,
 int bands, int has_overlay, int count, GLOBAL float* dst) {
 int p=PIXEL;
 if(p>=count) return;
 double components[9];
 for(int k=0;k<9;k++) components[k]=(double)src[p*9+k];
 double rgb[3]={0.0,0.0,0.0};
 for(int n=0;n<bands;n++) {
   double density=0.0;
   for(int k=0;k<9;k++) density+=components[k]*absorbance[k*bands+n];
   if(has_overlay) {
     double extra=0.0;
     for(int c=0;c<3;c++) extra+=(double)overlay[p*3+c]*basis[n*3+c];
     density=fmax(density+extra,0.0);
   }
   double t=pow(10.0,-fmin(density,80.0));
   for(int c=0;c<3;c++) rgb[c]+=t*weights[c*bands+n];
 }
 for(int c=0;c<3;c++) dst[p*3+c]=(float)rgb[c];
}
'''


PAPER_RESPONSE = r'''
KERNEL void paper_response(GLOBAL const float* src, GLOBAL const float* param,
 int count, GLOBAL float* dst) {
 int p=PIXEL;
 if(p>=count) return;
 int kind=(int)param[0];
 float rgb[3];
 for(int c=0;c<3;c++) rgb[c]=src[p*3+c]*param[1];
 for(int c=0;c<3;c++) {
   float dose=rgb[c];
   if(kind==1) dose=rgb[2]+rgb[1]*(c==0?.025f:c==1?.2f:1.f);
   else if(kind!=3) dose=rgb[2]+.08f*rgb[1];
   float x=-param[3]*(logf(fmaxf(dose,1.e-30f))-param[2]);
   float activated=expf(-(fmaxf(x,0.f)+log1pf(expf(-fabsf(x)))));
   if(kind==2) activated=1.f-activated;
   dst[p*3+c]=activated+(1.f-activated)*param[4];
 }
}
'''


ROTARY_CONTACT = r'''
INLINE double rotary_partial(double phase, double* activity, double seconds, double fraction, double period) {
 double wet=fmin(seconds,fmax(fraction-phase,0.0)*period), dry=seconds-wet;
 double active=wet>0.0?1.0:*activity;
 *activity=active*exp(-dry/5.0);
 return wet+active*5.0*(-expm1(-dry/5.0));
}
INLINE double rotary_step(double* phase, double* activity, double seconds, double fraction, double rpm, double retention) {
 if(seconds<=0.0) return 0.0;
 if(rpm==0.0) {
   double tau=5.0+retention*exp(-fmax(*phase-fraction,0.0)/.012);
   double dose=*phase<fraction?seconds:*activity*tau*(-expm1(-seconds/tau));
   *activity=*phase<fraction?1.0:*activity*exp(-seconds/tau);
   return dose;
 }
 if(fraction<=0.0 || fraction>=1.0) {
   double dose=fraction>=1.0?seconds:*activity*5.0*(-expm1(-seconds/5.0));
   *activity=fraction>=1.0?1.0:*activity*exp(-seconds/5.0);
   *phase+=seconds/(60.0/rpm); *phase-=floor(*phase);
   return dose;
 }
 double period=60.0/rpm, first=fmin(seconds,(1.0-*phase)*period);
 double dose=rotary_partial(*phase,activity,first,fraction,period);
 double remaining=fmax(seconds-first,0.0), turns=floor(remaining/period);
 double tail=fmax(remaining-turns*period,0.0), dry=(1.0-fraction)*period;
 dose+=turns*(fraction*period+5.0*(-expm1(-dry/5.0)));
 if(turns>0.0) *activity=exp(-dry/5.0);
 dose+=rotary_partial(0.0,activity,tail,fraction,period);
 *phase+=seconds/period; *phase-=floor(*phase);
 return dose;
}
KERNEL void rotary_contact(GLOBAL const double* plan, int intervals, int height, int width,
 int row_start, int count, double fraction, double base_rpm, double left, double right,
 double gradient, double retention, GLOBAL float* dst) {
 int p=PIXEL; if(p>=count) return;
 double x=((double)(p%width)+.5)/(double)width-.5;
 double local_fraction=gradient==0.0?fraction:fmin(fmax(fraction+gradient*x,0.0),1.0);
 x*=(double)width/(double)height;
 double y=((double)(row_start+p/width)+.5)/(double)height-.5;
 double activity=0.0,dose=0.0;
 for(int i=0;i<intervals;i++) {
   int k=i*7;
   double begin=plan[k],finish=fmin(plan[k+1],right);
   if(finish<=begin) break;
   double phase=.5+y*plan[k+5]+x*plan[k+6]+plan[k+4]+plan[k+2]*base_rpm/60.0;
   phase-=floor(phase);
   double before=fmax(0.0,fmin(finish,left)-begin);
   if(before>0.0) rotary_step(&phase,&activity,before,local_fraction,plan[k+3],retention);
   double seconds=finish-begin-before;
   if(seconds>0.0) dose+=rotary_step(&phase,&activity,seconds,local_fraction,plan[k+3],retention);
 }
 dst[p]=(float)fmin(fmax(dose,0.0),right-left);
}
'''


EDGE_CONTACT = r'''
KERNEL void edge_contact(GLOBAL const double* plan, int intervals, int height, int width,
 int row_start, int count, double left, double right, double physical_height,
 double transition_width, double retention, double direction, GLOBAL float* dst) {
 int p=PIXEL; if(p>=count) return;
 double y=(((double)(row_start+p/width)+.5)/(double)height)*physical_height;
 if(direction<0.0) y=physical_height-y;
 double activity=0.0,dose=0.0;
 for(int i=0;i<intervals;i++) {
   int k=i*(width+4);
   double begin=plan[k],end=fmin(plan[k+1],right),mix=plan[k+2],rate=plan[k+3];
   if(end<=begin) break;
   double distance=y-plan[k+4+p%width];
   double wet=fmin(fmax(distance/transition_width+.5,0.0),1.0);
   wet=wet*wet*(3.0-2.0*wet);
   double target=wet*(1.0-mix)+mix*rate;
   double tau=5.0+(mix==0.0?retention*exp(-fmax(-distance,0.0)/transition_width):0.0);
   activity=fmax(activity,target);
   double before=fmax(0.0,fmin(end,left)-begin);
   if(before>0.0) activity=target+(activity-target)*exp(-before/tau);
   double seconds=end-begin-before;
   if(seconds>0.0) {
     dose+=target*seconds+(activity-target)*tau*(-expm1(-seconds/tau));
     activity=target+(activity-target)*exp(-seconds/tau);
   }
 }
 dst[p]=(float)fmin(fmax(dose,0.0),right-left);
}
'''


COORDINATE_NOISE = r'''
INLINE unsigned int dither_mix32(unsigned int v) {
 v^=v>>16; v*=0x7FEB352Du; v^=v>>15; v*=0x846CA68Bu; v^=v>>16; return v;
}
KERNEL void coordinate_noise(int row_start, int width, int height, int count, GLOBAL float* dst) {
 int p=PIXEL; if(p>=count) return;
 int x=p%width,y=row_start+p/width;
 int sx=((x-5)%width+width)%width,sy=((y-3)%height+height)%height;
 unsigned int a=dither_mix32((unsigned int)x)^dither_mix32((unsigned int)y^0xA511E9B3u);
 unsigned int b=dither_mix32((unsigned int)sx)^dither_mix32((unsigned int)sy^0xA511E9B3u);
 unsigned int salts[3]={0x9E3779B9u,0x85EBCA6Bu,0xC2B2AE35u};
 for(int c=0;c<3;c++) {
   float left=(float)dither_mix32(a^salts[c])/4294967296.f-.5f;
   float right=(float)dither_mix32(b^salts[c])/4294967296.f-.5f;
   dst[p*3+c]=(left+right)*.5f;
 }
}
'''


DEVELOPMENT_CONVERSION = r'''
KERNEL void development_conversion(GLOBAL const float* halide, GLOBAL const float* activation,
 GLOBAL const float* local, GLOBAL const float* fog_local, GLOBAL const float* rates,
 int layers, int local_mode, int fog_mode, float exponent, float compensation,
 float fog_coefficient, int count, GLOBAL float* dst) {
 int i=PIXEL; if(i>=count) return;
 int c=i%layers;
 float e=activation[i], h=halide[i];
 float rate=rates[c];
 if(exponent!=0.f) rate=powf(fmaxf(e,1.e-6f)/.5f,exponent)*rate;
 if(compensation>0.f) {
   float g=fminf(fmaxf((e-.35f)/.65f,0.f),1.f);
   float factor=g*g; factor*=3.f-2.f*g;
   factor*=compensation; factor=1.f-factor;
   rate*=factor;
 }
 if(local_mode) rate*=local[local_mode==1?i/layers:i];
 rate=fminf(fmaxf(rate,0.f),1.f);
 float reactive=h*e;
 float signal=fminf(reactive*rate,reactive);
 float fr=rates[layers+c];
 if(fog_mode) fr*=fmaxf(fog_local[fog_mode==1?i/layers:i],0.f);
 float fraction=-expm1f(fog_coefficient*fr);
 float fog=fmaxf(h-reactive,0.f)*fminf(fmaxf(fraction,0.f),1.f);
 dst[i]=signal+fog;
 dst[count+i]=reactive-signal;
 dst[count*2+i]=fog;
}
'''


def source(name, backend):
    if name=='edge_contact_f32':
        # A real single-precision kernel, including literals and transcendentals.
        # Keep one formula source so CPU/CUDA/OpenCL share the same wet history.
        import re
        body=EDGE_CONTACT.replace('edge_contact(', 'edge_contact_f32(').replace('double','float')
        for function in ('fmin','fmax','exp','expm1'):
            body=re.sub(r'\b'+function+r'\(',function+'f(',body)
        body=re.sub(r'(?<![\w.])(\d*\.\d+)(?![\w.])',r'\1f',body)
        return source('dye',backend).split(DYE)[0]+body
    body = {'dye': DYE, 'spectral': SPECTRAL, 'paper_response':PAPER_RESPONSE, 'rotary_contact':ROTARY_CONTACT,'edge_contact':EDGE_CONTACT,'coordinate_noise':COORDINATE_NOISE,'development_conversion':DEVELOPMENT_CONVERSION}[name]
    if backend == 'cuda':
        header = '#define INLINE __device__ inline\n#define KERNEL extern "C" __global__\n#define GLOBAL\n#define PIXEL (blockDim.x*blockIdx.x+threadIdx.x)\n'
    elif backend == 'opencl':
        header = ('#pragma OPENCL FP_CONTRACT OFF\n#define INLINE inline\n#define KERNEL __kernel\n'
                  '#define GLOBAL __global\n#define PIXEL get_global_id(0)\n'
                  '#define fmaxf fmax\n#define fminf fmin\n#define powf pow\n#define log10f log10\n'
                  '#define logf log\n#define expf exp\n#define expm1f expm1\n#define log1pf log1p\n#define fabsf fabs\n')
        if name in ('spectral','rotary_contact','edge_contact'):
            header = '#pragma OPENCL EXTENSION cl_khr_fp64 : enable\n' + header
    else:
        raise ValueError('Unsupported kernel backend')
    return header + body
