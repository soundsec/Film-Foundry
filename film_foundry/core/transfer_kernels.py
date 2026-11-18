"""Finite transfer's four time intervals in one CUDA/OpenCL dispatch.

Only the original per-row rates are prepared on the host. Reagent inventories,
12-node quadrature, light competition and acid arrest remain in FP64 registers
until the complete advance is written back to FP32 material storage.
"""

TRANSFER_ADVANCE = r'''
INLINE void transfer_interval(double dt,double rate,double light,double b,double acid,
 double yield,int neutralizing,
 double *remaining,double *immobile,double *received,double *donor,double *loss,
 double *developer,double *used,double *stain_sum,GLOBAL const double* quad) {
 if(dt==0.0)return;
 double wet=acid>0.0?fmin(dt,b/acid):dt;
 double clock=b*wet-.5*acid*wet*wet;
 double substrate=fmax(*remaining+*immobile-*donor,0.0);
 double gap=fabs(substrate-*developer), least=fmin(substrate,*developer);
 double exposure=rate*clock;
 double y=gap>0.0?-expm1(-gap*exposure)/gap:exposure;
 double consumed=fmin(least,substrate*(*developer)*y/(1.0+least*y));
 double fraction=substrate>0.0?consumed/substrate:0.0;
 double light_time=neutralizing?(b>0.0?wet:0.0):dt;
 double transferred,retained,fogged;
 if(light>0.0 && consumed>0.0) {
   double integral=0.0,greatest=fmax(substrate,*developer);
   if(*remaining>0.0)for(int k=0;k<12;k++) {
     double extent=consumed*((quad[k]+1.0)*.5);
     double den=fmax((least-extent)*greatest,1.e-300);
     double q=gap>1.e-12?log1p(extent*gap/den)/gap:extent/den;
     q=rate>0.0?q/rate:0.0;
     double divisor=b+sqrt(fmax(b*b-2.0*acid*q,0.0));
     double time=divisor>0.0?2.0*q/divisor:0.0;
     integral+=quad[12+k]*exp(-light*time);
   }
   transferred=substrate>0.0?*remaining*(consumed*.5*integral/substrate):0.0;
   retained=*remaining*(1.0-fraction)*exp(-light*light_time);
   double drop=*remaining-retained;
   transferred=fmin(fmin(transferred,consumed),drop);
   fogged=drop-transferred;
 } else {
   transferred=fmin(*remaining*fraction,consumed);
   retained=light>0.0?(*remaining-transferred)*exp(-light*light_time):*remaining-transferred;
   fogged=*remaining-transferred-retained;
 }
 *remaining=retained;*immobile+=fogged;*donor+=consumed-transferred;
 *received+=transferred*yield;*loss+=transferred*(1.0-yield);
 *developer=fmax(*developer-consumed,0.0);*used+=consumed;
 // Accumulate channel transfers first; the caller uses the original mean.
 *stain_sum+=transferred;
}
KERNEL void transfer_advance(GLOBAL const float* src,GLOBAL const int* offsets,
 GLOBAL const double* yields,GLOBAL const double* quad,
 double seconds,double delay,double release_seconds,int neutralizing,
 int channels,int count,GLOBAL float* dst) {
 int p=PIXEL;if(p>=count)return;
 double r[3],im[3],rec[3],donor[3],loss[3],dev[3],used[3],stain[3];
 for(int c=0;c<channels;c++) {
   int q=p*channels+c;
   r[c]=src[offsets[0]*count+q]; im[c]=src[offsets[1]*count+q];
   rec[c]=src[offsets[2]*count+q]; donor[c]=src[offsets[3]*count+q];
   loss[c]=src[offsets[4]*count+q];dev[c]=src[offsets[5]*count+q];used[c]=src[offsets[6]*count+q];
 }
 for(int c=0;c<3;c++)stain[c]=src[offsets[7]*count+p*3+c];
 double alkali=src[offsets[8]*count+p],neutralized=src[offsets[9]*count+p];
 double acid_left=src[offsets[10]*count+p],clock=src[offsets[11]*count+p];
 double before=src[offsets[15]*count+p],timer=src[offsets[16]*count+p];
 double acid=src[offsets[17]*count+p];
 double duration=(float)seconds-(float)before;
 double pre=timer>0.0?fmin(duration,fmax(delay-clock,0.0)/timer):duration;
 double left=duration-pre;
 double next_clock=clock+pre*timer;
 double release=timer>0.0?fmin(left,fmax(delay+release_seconds-next_clock,0.0)/timer):left;
 for(int stage=0;stage<4;stage++) {
   double dt=stage==0?before:(stage==1?pre:(stage==2?release:left-release));
   double sum=0.0;
   for(int c=0;c<channels;c++) {
     int q=p*channels+c;
     double rate=stage==0?0.0:src[offsets[12]*count+q];
     double light=src[offsets[stage==0?13:14]*count+q];
     transfer_interval(dt,rate,light,alkali,stage==2?acid:0.0,yields[q],
       stage==0?0:neutralizing,&r[c],&im[c],&rec[c],&donor[c],&loss[c],&dev[c],&used[c],&sum,quad);
   }
   if(stage>0)for(int c=0;c<3;c++)stain[c]+=(sum/channels)*quad[24+c];
   if(stage==2) {
     double amount=fmin(alkali,fmin(acid_left,acid*release));
     alkali-=amount;neutralized+=amount;acid_left-=amount;
   }
 }
 for(int c=0;c<channels;c++) {
   int q=p*channels+c;
   dst[offsets[0]*count+q]=(float)r[c];dst[offsets[1]*count+q]=(float)im[c];
   dst[offsets[2]*count+q]=(float)rec[c];dst[offsets[3]*count+q]=(float)donor[c];
   dst[offsets[4]*count+q]=(float)loss[c];dst[offsets[5]*count+q]=(float)dev[c];dst[offsets[6]*count+q]=(float)used[c];
 }
 for(int c=0;c<3;c++)dst[offsets[7]*count+p*3+c]=(float)stain[c];
 dst[offsets[8]*count+p]=(float)alkali;dst[offsets[9]*count+p]=(float)neutralized;
 dst[offsets[10]*count+p]=(float)acid_left;
 dst[offsets[11]*count+p]=(float)fmin(next_clock+left*timer,delay+release_seconds);
}
'''
