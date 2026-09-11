-- Read-only example. Does not include license or registration information.
return {
  tracks = reaper.CountTracks(0),
  tempo = reaper.Master_GetTempo(),
  can_set_fx_parameters = reaper.APIExists('TrackFX_SetParamNormalized')
}
