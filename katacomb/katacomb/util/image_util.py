import astropy.wcs as wcs
from astropy import units
import astropy.io.fits as fits
from matplotlib import use
use('Agg', warn=False)  # noqa: E402
import matplotlib.axes
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1 import make_axes_locatable
import numpy as np

import katsdpsigproc.zscale as zscale


DEFAULT_DPI = 96


def _prepare_axes(wcs, width, height, image_width, image_height, dpi, slices, bbox):
    fig = plt.Figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    ax = fig.add_subplot(projection=wcs, slices=slices)
    ax.set_xlabel('Right Ascension')
    ax.set_ylabel('Declination')
    ax.set_xlim(-0.5 + bbox[0], bbox[1] + 0.5)
    ax.set_ylim(-0.5 + bbox[2], bbox[3] + 0.5)
    return fig, ax


def _plot(data, bunit, caption, ax, extent, vmin, vmax, facecolor):
    if bunit == 'JY/BEAM':
        # This is not FITS-standard, but is AIPS standard and is output by AIPS generated FITS images
        # as well as older versions of katsdpimager
        unit = units.Jy / units.beam
    else:
        unit = units.Unit(bunit)
    data <<= unit
    vmin <<= unit
    vmax <<= unit
    # If the flux is low, use µJy/beam or mJy/beam to keep scale sane
    if vmax < 100 * (units.uJy / units.beam):
        data = data.to(units.uJy / units.beam)
    elif vmax < 100 * (units.mJy / units.beam):
        data = data.to(units.mJy / units.beam)
    vmin = vmin.to(data.unit)
    vmax = vmax.to(data.unit)
    if not ax.images:
        im = ax.imshow(data.value, origin='lower', cmap='afmhot', aspect='equal',
                       vmin=vmin.value, vmax=vmax.value, extent=extent)
        divider = make_axes_locatable(ax)
        cax = divider.append_axes('right', pad='3%', size='5%', axes_class=matplotlib.axes.Axes)
        cbar = ax.get_figure().colorbar(im, cax=cax, orientation='vertical')
        # simply using data.unit.to_string('unicode') draws an ASCII-art
        # fraction, which doesn't end up looking very good. But we want
        # unicode to properly render µJy (rather than uJy).
        unit_label = (data.unit * units.beam).to_string('unicode') + ' / beam'
        cbar.set_label(unit_label)
    else:
        im = ax.images[0]
        im.set_data(data)
        im.set_extent(extent)

    if caption:
        ax.set_title(f'{caption}')

    if facecolor:
        ax.set_facecolor(facecolor)


def write_image(input_file, output_file, width=1024, height=768, dpi=DEFAULT_DPI,
                slices=('x', 'y', 0, 0), caption=None, facecolor=None):
    """Write an image plane to a file from a single FITS file.

    Parameters
    ----------
    input_file : str
        Source FITS file
    output_file : str
        Output image file, including extension
    width, height : int
        Dimensions of output image
    dpi : int
        DPI of output image
    slices : list
        Choice of image dimensions. Passed to :class:`WCSAxes`
    caption : Optional[str]
        Optional caption to include in the image
    facecolor : Optional[str]
        Optional background color to use in the image plot window.
        Blanked pixels in the input FITS image will appear in this color.
    """

    with fits.open(input_file) as hdus:
        ax_select = tuple(slice(None) if s in ('x', 'y') else s for s in slices[::-1])
        data = hdus[0].data[ax_select]
        vmin, vmax = zscale.zscale(zscale.sample_image(data))
        image_height, image_width = data.shape
        # Work out bounding box surrounding finite data
        # Plot the lot if any axis is completely blanked
        finite_data = np.where(np.isfinite(data))
        bbox = (0, image_width - 1, 0, image_height - 1)
        if finite_data[0].size > 0:
            ymin = np.min(finite_data[0])
            ymax = np.max(finite_data[0])
            xmin = np.min(finite_data[1])
            xmax = np.max(finite_data[1])
            bbox = (xmin, xmax, ymin, ymax)
        fig, ax = _prepare_axes(wcs.WCS(hdus[0]), width, height, image_width, image_height, dpi, slices, bbox)
        bunit = hdus[0].header['BUNIT']
        _plot(data, bunit, caption, ax, None, vmin, vmax, facecolor)
        fig.savefig(output_file)
