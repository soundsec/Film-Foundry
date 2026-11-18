"""Meter only layer exposure seen by the material and its actual optical master."""
from threading import Lock

import numpy as np


class PreviewProbe:
    edges = np.linspace(-6.,4.,101)

    def __init__(self):
        self.lock = Lock()
        self.density = self.curves = self.bins = None
        self.expected_pixels = None
        self.scan_geometry = None
        self.count = 0
        self.shape = self.seen_rows = self.log_exposure = None

    def begin_exposure(self, shape):
        self.shape = tuple(shape)
        self.seen_rows = np.zeros(shape[0], dtype=bool)
        self.expected_pixels = shape[0] * shape[1]

    def exposure(self, material, exposure, start):
        # Only meter photographed rows. Auxiliary work grids precede
        # begin_exposure; factory-border samples arrive after the frame.
        if self.shape is None or exposure.shape[1] != self.shape[1]:
            return
        stop = start + exposure.shape[0]
        with self.lock:
            if stop > self.shape[0] or self.seen_rows[start:stop].any():
                return
            self.seen_rows[start:stop] = True
            layers = material.layer_count
            if self.log_exposure is None:
                self.log_exposure = np.empty((*self.shape, layers), dtype=np.float32)
                self.bins = np.zeros((layers, len(self.edges)-1), dtype=np.int64)
                doses = np.repeat((10.**self.edges)[:,None], layers, axis=1).astype(np.float32)
                self.curves = material.expose(doses).developability.copy()
            local = self.log_exposure[start:stop]
            np.maximum(exposure, 1.e-6, out=local)
            np.log10(local, out=local)
            for i in range(layers):
                self.bins[i] += np.histogram(np.clip(local[..., i], self.edges[0], self.edges[-1]), self.edges)[0]
            self.count += local.shape[0] * local.shape[1]

    def use_profile(self, profile):
        if profile is not None:
            self.bins, self.curves, self.count, self.log_exposure = profile

    def profile(self):
        if self.bins is not None:
            return self.bins, self.curves, self.count, self.log_exposure

    def exposure_at(self, x, y, width, height, aperture=1):
        if self.log_exposure is None or self.count != self.expected_pixels:
            return None
        h,w = self.log_exposure.shape[:2]
        # Formed rebate adds top/bottom rows; the scanner reference adds an
        # equal border to each edge. Neither contains photographed scene H.
        dx,dy = width-w,height-h
        if dx < 0 or dy < dx or dx%2 or dy%2:
            return None
        x,y = x-dx//2,y-dy//2
        return self.sample(self.log_exposure, x, y, aperture)

    def medium(self, medium):
        self.density = medium.optical_density_rgb

    def density_at(self, x, y, width, height, aperture=1):
        if self.density is None:return None
        h,w = self.density.shape[:2]
        dw,dh = width-w,height-h
        if dw != dh or dw < 0 or dw%2:return None
        x,y = x-dw//2,y-dh//2
        return self.sample(self.density, x, y, aperture)

    @staticmethod
    def sample(image, x, y, aperture):
        h,w=image.shape[:2]
        if not (0<=x<w and 0<=y<h):return None
        if aperture==1:return image[y,x]
        radius=aperture//2
        return image[max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1].mean(axis=(0,1))
