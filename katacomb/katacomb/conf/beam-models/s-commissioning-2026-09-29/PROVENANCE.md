# S-band commissioning beams, 2026-09-29

Local, non-public commissioning assets. Do not publish or push this image.

MK (13.5 m): beam_mavg_S04.fits from commissioning-2026-09-29,
split with the existing beam_conversion.py. Coverage 1.763671875--3.486328125 GHz.
MKE (15 m): derived MK+S04_sym_LM.fits, generated from MK+S0_sym.fits
and MK+S4_sym.fits. Comparison at 2.45, 2.55,
2.70, 2.80 and 3.00 GHz strongly favours the known extension model over MK.
At 2.45 GHz half-power radii are 0.292696 deg (MK+), 0.292587 (MKE),
0.339073 (MK). See antenna_comparison.json for all measurements.

The combined cube uses katbeam's direct axis1=L, axis2=M interpretation,
retains VV/HH/VH/HV ordering, and linearly interpolates complex values.
One output frequency plane at 2.625 GHz bridges a gap between observations.

HV phase correction is outstanding (Mattieu 2026-09-29).

Spatial coverage differs: MK is -0.5 to +0.484375 deg per axis; MK+
is -2 to +1.96875 deg. Do not use the original FOV=1.0/OutlierDist=1.75
without addressing the MK beam spatial limit.

Advised to use FOV=0.4 and OutlierDist=0.45.
