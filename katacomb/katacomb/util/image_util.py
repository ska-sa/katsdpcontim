import numpy as np

from matplotlib import use
use('Agg', warn=False)
from matplotlib import pylab as plt  # noqa: E402

import katsdpsigproc.zscale as zscale


def save_image(image, filename, plane=1, drop_edge=0.125, cmap='afmhot',
               display_size=10., dpi=1000):
    """
    Write an image plane to a file using imshow with contrast scaling.

    Parameters
    ----------
    image : :class:`ImageFacade`
        The image to plot
    filename : str
        Disk location of output. Filename extension determins
        format of the output as for `matplotlib.pylab.savefig`
    plane : int
        The image plane to plot
    drop_edge : float
        Fraction of pixels on each edge of the image to clip.
    cmap : str or :class:`matplotlib.colors.Colormap`
        Matplotlib style colormap to use (passed to `imshow`)
    display_size : float
        Size in inches for the `matplotlib.pylab.figure` instance.
    dpi : int
        Dots per inch desired for figure.
        (Passed to `matplotlib.pylab.savefig`)
    """

    # Get the image array for the desired plane
    image.GetPlane(None, [plane, 1, 1, 1, 1])
    imagedata = image.np_farray

    xpix, ypix = imagedata.shape

    # Determine amount of image edge to remove
    x_off = int(xpix * drop_edge)
    y_off = int(ypix * drop_edge)

    # Cut the image to the desired display size
    x_keep = slice(x_off, xpix - x_off)
    y_keep = slice(y_off, ypix - y_off)
    cutimagedata = imagedata[x_keep, y_keep]

    # Get low and high pixel values to stretch the comormap
    lowcut, highcut = zscale.zscale(zscale.sample_image(cutimagedata))

    im = plt.figure(figsize=(display_size, display_size))

    # Remove axes and whitespace around edges
    plt.axis('off')
    plt.axes([0, 0, 1, 1])

    # Plot the image with the desired colormap and contrast
    plt.imshow(cutimagedata, cmap=cmap, vmin=lowcut, vmax=highcut,
               aspect='equal', origin='lower', interpolation='nearest')

    # Save to filename at the desired dpi
    plt.savefig(filename, dpi=dpi)
    plt.close(im)
