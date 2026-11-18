"""Retain at most one bounded formal image while the user moves a region."""
from film_foundry.contracts.scan_framing import pixel_bounds, rectangle


class RegionInspection:
    def __init__(self,session,*,scaled=False,cache_bytes=32*1024*1024):
        self.session=session
        self.scaled=scaled
        self.cache_bytes=cache_bytes
        self.index=None
        self.frame=None

    def read(self,index,config,rect):
        """Index identifies a frozen choice within this inspection dialog."""
        rect=rectangle(rect)
        self.session.check_source()
        if index!=self.index or self.frame is None:
            self.frame=None
            frame=self.session.formal_frame(config,scaled_override=self.scaled)
            self.session.check_source()
            self.index=index
            if frame.nbytes<=self.cache_bytes:self.frame=frame
        else:frame=self.frame
        l,t,r,b=pixel_bounds(frame.shape,rect)
        return frame[t:b,l:r].copy(),(l,t,r,b),frame.shape[:2]
