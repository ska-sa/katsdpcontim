import katsdpmodels.fetch.requests
import katsdpmodels.band_mask
import katsdpmodels.rfi_mask

import logging
logger = logging.getLogger('katacomb')


import requests

import numpy as np
import astropy.units as u



def _get_band_mask(telstate_l0):
    with katsdpmodels.fetch.requests.TelescopeStateFetcher(telstate_l0) as fetcher:
        correlator_stream = telstate_l0['src_streams'][0]  
        f_engine_stream = telstate_l0.view(correlator_stream, exclusive=True)['src_streams'][0]
        telstate_cbf = telstate_l0.view(f_engine_stream, exclusive=True)
        band_mask_model_key = telstate_l0.join('model', 'band_mask', 'fixed')
        try:
            band_mask_model = fetcher.get(band_mask_model_key,
                                          katsdpmodels.band_mask.BandMask,
                                          telstate=telstate_cbf)
            return band_mask_model
        except (requests.ConnectionError, katsdpmodels.models.ModelError) as exc:
            logger.warning('Failed to load band_mask model:', exc)
            return None


def _get_rfi_mask(telstate_l0):
    with katsdpmodels.fetch.requests.TelescopeStateFetcher(telstate_l0) as fetcher:
        rfi_mask_model_key = telstate_l0.join('model', 'rfi_mask', 'fixed')
        try:
            rfi_mask_model = fetcher.get(rfi_mask_model_key,
                                         katsdpmodels.rfi_mask.RFIMask)
            return rfi_mask_model
        except (requests.ConnectionError, katsdpmodels.models.ModelError) as exc:
            logger.warning('Failed to load rfi_mask model: %s', exc)
            return None

def get_static_mask(telstate_l0, channel_freqs, length=100.0):
    """Get the static mask for the given frequencies and baseline length.

    Parameters:
    -----------
    telstate_l0 : :class:`katsdptelstate.TelescopeState`
        Telescope state with a view of the L0 attributes
    channel_freqs : :class:`~astropy.units.Quantity`
        frequencies
    length, optional : float
        baseline length in m
    """
    rfi_mask = _get_rfi_mask(telstate_l0)
    band_mask = _get_band_mask(telstate_l0)

    bandwidth = telstate_l0['bandwidth']
    channel_width = bandwidth / telstate_l0['n_chans']
    if rfi_mask is None:
        static_mask = np.zeros((1, len(channel_freqs)))
    else:
        static_mask = rfi_mask.is_masked(channel_freqs, length * u.m, channel_width * u.Hz)

    if band_mask is not None:
        center = telstate_l0['center_freq']
        band_spw = katsdpmodels.band_mask.SpectralWindow(bandwidth * u.Hz, center * u.Hz)
        mask = band_mask.is_masked(band_spw, channel_freqs, channel_width * u.Hz)
        static_mask |= mask
    return static_mask

