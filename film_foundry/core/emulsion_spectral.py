"""Area mixture of intact emulsion and bare support, integrated by wavelength."""
import numpy as np
from film_foundry.contracts.spectral_optics import post_process_rgb_weights


def capture_coverage(values,coverage,covered,overlay,weights,absorbance,wavelengths):
    result=np.empty(values.shape[:2]+(3,),np.float32)
    for start in range(0,values.shape[0],128):
        stop=min(values.shape[0],start+128);source=values[start:stop];c=coverage[start:stop]
        accum=np.zeros(source.shape[:2]+(3,),np.float64)
        for band,wave in enumerate(wavelengths):
            coefficients=absorbance[:,band]
            support=source[...,6]*coefficients[6]+source[...,8]*coefficients[8]
            emulsion=np.einsum('...k,k->...',source,coefficients)-support
            basis=post_process_rgb_weights(wave)
            if covered is not None:emulsion+=np.einsum('...c,c->...',covered[start:stop],basis)
            np.maximum(emulsion,0.,out=emulsion)
            if overlay is not None:support=support+np.einsum('...c,c->...',overlay[start:stop],basis)
            np.maximum(support,0.,out=support)
            transmission=np.power(10.,-np.minimum(support,80.))*((1-c)+c*np.power(10.,-np.minimum(emulsion,80.)))
            accum+=transmission[...,None]*weights[:,band]
        result[start:stop]=accum
    return result
