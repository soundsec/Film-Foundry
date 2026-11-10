"""Roll display segmentation and read-only film observation."""


def roll_segments(data,length=6):
    if length not in (4,6):raise ValueError('Strip display length must be 4 or 6')
    if data['phase'] not in ('developed','scanned'):return []
    ordered=[{'frame_index':f['frame_index'],**data['frames'][str(f['frame_index'])]} for f in data['roll']['frames']]
    return [ordered[i:i+length] for i in range(0,len(ordered),length)]


class RollLightTable:
    """Single-worker owner of one reduced viewing copy, never a forming state."""
    def __init__(self):self.path=None;self.medium=None

    def observe(self,path,ev=0.,temperature=5500.,*,mode='transmission',light_angle=30.,view_angle=0.,background=.02):
        from film_foundry.app.film_observation import FilmViewingSettings,load_observable_medium,observe
        settings=FilmViewingSettings(mode,ev,temperature,light_angle,view_angle,background)
        if path!=self.path:
            self.medium=None;self.path=None
            self.medium=load_observable_medium(path,preview_long_edge=1600);self.path=path
        return observe(self.medium,settings)
