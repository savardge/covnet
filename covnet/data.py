#!/usr/bin/env python
# -*- coding: utf-8 -*-

import obspy
import numpy as np
import warnings

from datetime import datetime
from matplotlib import pyplot as plt
from matplotlib import dates as md
from scipy.signal import stft, istft
from statsmodels import robust

from . import logtable
from . import maths

# Ignore FutureWarning generated by np.floating in h5py.__init__
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    import h5py


def read(*args):
    """ (Top-level). Read the data files specified in the datapath with
        arrayprocessing.Stream.read.

    This method uses the obspy's read method itself. A check for the
    homogeneity of the seismic traces (same number of samples) is done a
    the end. If the traces are not of same size, an warning message
    shows up.

    No homogeneity check is returned by the function.

    Arguments:
    ----------
        data_path (str or list): path to the data. Path to a single file
            (str), to several files using UNIX regexp (str), or a list of
            files (list). See obspy.read method for more details.
    """

    data = Stream()
    data.read(*args)
    return data


def h5read(*args, **kwargs):
    """ Top-level read function, returns Stream object.
    """

    data = Stream()
    data.h5read(*args, **kwargs)
    return data


def matread(*args, **kwargs):
    """ Top-level read function, returns Stream object.
    """

    data = Stream()
    data.matread(*args, **kwargs)
    return data


class Stream(obspy.core.stream.Stream):

    def __init__(self, *args, **kwargs):
        super(Stream, self).__init__(*args, **kwargs)

    @property
    def times(self):
        """ Extracts time from the first trace and return matplotlib time.
        """

        # Obspy times are seconds from starttime
        times = self[0].times()

        # Turn into day fraction
        times /= 24 * 3600

        # Add matplotlib starttime in days
        start = self[0].stats.starttime.datetime
        times += md.date2num(start)

        return times

    @property
    def stations(self):
        """List all the station names extracted from each trace.
        """
        return [s.stats.station for s in self]

    def read(self, data_path):
        """ Read the data files specified in the datapath with obspy.

        This method uses the obspy's read method itself. A check for the
        homogeneity of the seismic traces (same number of samples) is done a
        the end. If the traces are not of same size, an warning message
        shows up.

        Arguments:
        ----------
            data_path (str or list): path to the data. Path to a single file
                (str), to several files using UNIX regexp (str), or a list of
                files (list). See obspy.read method for more details.

        Return:
        -------
            homogeneity (bool): True if the traces are all of the same size.

        """

        # If data_path is a str, then only a single trace or a bunch of
        # seismograms with regexp
        if isinstance(data_path, str):
            waitbar = logtable.waitbar('Read seismograms', 1)
            self += obspy.read(data_path)
            waitbar.progress(0)

        # If data_path is a list, read each fils in the list.
        elif isinstance(data_path, list):
            waitbar = logtable.waitbar('Read seismograms', len(data_path))
            for index, path in enumerate(data_path):
                self += obspy.read(path)
                waitbar.progress(index)

    def h5read(self, path_h5, net='PZ', force_start=None, stations=None,
               channel='Z', trim=None):
        """Read seismograms in h5 format.

        Parameters
        ----------
            path_h5 : str
                Path to the h5 file.

        Keyword arguments
        -----------------
            net : str
                Name of the network to read.
            force_start : str
                The date at which the seismograms are supposed to start.
                Typically this is at midnight at a given day.
            channel : str
                The channel to extract; either "Z", "E" or "N"
            stations : list
                A list of desired seismic stations.
            trim : list
                A list of trim dates as strings. This allows to extract only
                a small part of the seismograms without loading the full day,
                and therefore to considerably improve the reading efficiency.
        """

        # Open file
        h5file = h5py.File(path_h5, 'r')

        # Meta data
        # ---------

        # Initialize stream header
        stats = obspy.core.trace.Stats()

        # Sampling rate
        sampling_rate = np.array(h5file['_metadata']['fe'])
        stats.sampling_rate = sampling_rate

        # Starting time
        if force_start is None:
            start = np.array(h5file['_metadata']['t0_UNIX_timestamp'])
            stats.starttime = obspy.UTCDateTime(datetime.fromtimestamp(start))
        else:
            stats.starttime = obspy.UTCDateTime(force_start)

        # Station codes
        if stations is None:
            station_codes = [k for k in h5file[net].keys()]
        else:
            station_codes = [k for k in h5file[net].keys() if k in stations]

        # Data extaction
        # --------------

        # Define indexes of trim dates in order to extract the time segments.
        # This modifies the start time to the start trim date.
        if trim is not None:
            i_start = int(obspy.UTCDateTime(trim[0]) - stats.starttime)
            i_end = int(obspy.UTCDateTime(trim[1]) - stats.starttime)
            i_start *= int(sampling_rate)
            i_end *= int(sampling_rate)
            stats.starttime = obspy.UTCDateTime(trim[0])
        else:
            i_start = 0
            i_end = -1

        # Collect data into stream
        waitbar = logtable.waitbar('Read data', len(station_codes))
        for station, station_code in enumerate(station_codes):
            waitbar.progress(station)

            # Tries to read the data for a given station. This raises
            # a KeyError if the station has no data at this date.
            try:

                # Read data
                data = h5file[net][station_code][channel][i_start:i_end]

                # Include the specs of this trace in the corresponding header
                stats.npts = len(data)
                stats.station = station_code.split('.')[0]

                # Add to the main stream
                self += obspy.core.trace.Trace(data=data, header=stats)

            # If no data is present for this day at this station, nothing is
            # added to the stream. This may change the number of available
            # stations at different days.
            except KeyError:
                continue

    def matread(self, data_path, data_name='data', starttime=0,
                sampling_rate=25.0, decimate=1):
        """
        Read the data files specified in the datapath.

        Arguments
        ---------
        :datapath (str or list): datapath with a single data file or with
        UNIX regexp, or a list of files.

        Keyword arguments
        -----------------

        :sort (bool): whether or not the different traces are sorted in
        alphabetic order with respect to the station codes.
        """

        # Read meta
        traces = np.array(h5py.File(data_path, 'r')[data_name])
        n_stations, n_times = traces.shape

        # Header
        stats = obspy.core.trace.Stats()
        stats.sampling_rate = sampling_rate
        stats.npts = n_times

        # Start time
        stats.starttime = obspy.UTCDateTime(starttime)

        # Collect data into data np.array
        waitbar = logtable.waitbar('Read data')
        for station in range(0, n_stations, decimate):
            waitbar.progress((station + 1) / n_stations)
            data = traces[station, :]
            self += obspy.core.trace.Trace(data=data, header=stats)

    def set_data(self, data_matrix, starttime, sampling_rate):
        """
        Set the data from any external set of traces.
        """

        n_traces, n_times = data_matrix.shape

        # Header
        stats = obspy.core.trace.Stats()
        stats.sampling_rate = sampling_rate
        stats.starttime = obspy.UTCDateTime(starttime)
        stats.npts = n_times

        # Assign
        waitbar = logtable.waitbar('Read data')
        for trace_id, trace in enumerate(data_matrix):
            waitbar.progress((trace_id + 1) / n_traces)
            self.data = self.trace

    def cut(self, starttime, endtime, pad=True, fill_value=0):
        """Cut seismic traces between given start and end times.

        A wrapper to the :meth:`obspy.Stream.trim` method with string dates or
        datetimes.

        Parameters
        ----------
        starttime : str
            The starting date time.

        endtime : str
            The ending date time.

        Keyword arguments
        -----------------

        pad : bool
            Whether the data has to be padded if the starting and ending times
            are out of boundaries.

        fill_value : int, float or str
            Specifies the values to use in order to fill gaps, or pad the data
            if ``pad`` is set to True.

        """

        # Convert date strings to obspy UTCDateTime
        starttime = obspy.UTCDateTime(starttime)
        endtime = obspy.UTCDateTime(endtime)

        # Trim
        self.trim(starttime=starttime, endtime=endtime, pad=pad,
                  fill_value=fill_value)

    def homogenize(self, sampling_rate=20.0, method='linear',
                   start='2010-01-01', npts=24 * 3600 * 20):
        """
        Same prototype than homogenize but allows for defining the date in str
        format (instead of UTCDateTime).
        Same idea than with the cut method.
        """
        start = obspy.UTCDateTime(start)
        self.interpolate(sampling_rate, method, start, npts)

    def binarize(self, epsilon=1e-10):
        """Binarization of the seismic traces in temporal domain.

        Considering :math:`x(t)` being the seismic trace, the binarized trace
        :math:`x_b(n)` is obtained by

        .. math::
            x_b(t) = \\frac{x(t)}{|x(t)| + \\epsilon}

        where :math:`\\epsilon > 0` is a small regularization value.

        Keyword arguments
        -----------------
        epsilon : float
            Regularization value for division.

        """

        # Waitbar initialization
        n_traces = len(self)
        waitbar = logtable.waitbar('Binarize', n_traces)

        # Binarize
        for index, trace in enumerate(self):
            waitbar.progress(index)
            trace.data = trace.data / (np.abs(trace.data) + epsilon)

    def stationarize(self, length=11, order=1, epsilon=1e-10):
        """ Trace stationarization with smoothing time enveloppe.

        Args
        ----
            length (int): length of the smoothing window.

        """

        # Waitbar initialization

        waitbar = logtable.waitbar('Stationarize')
        n_traces = len(self)

        # Binarize
        for index, trace in enumerate(self):
            waitbar.progress((index + 1) / n_traces)
            smooth = maths.savitzky_golay(np.abs(trace.data), length, order)
            trace.data = trace.data / (smooth + epsilon)

    def demad(self):
        """ Normalize traces by their mean absolute deviation (MAD).

        The Mean Absolute Deviation :math:`m_i` of the trace :math:`i`
        describe the deviation of the data from its average :math:`\\bar{x}_i`
        obtained by the formula

        .. math::
            m_i = \\frac{1}{K}\\sum_{k=1}^{K}|x_i[k] - \\bar{x}_i|,

        where :math:`k` is the time index of the sampled trace. Each trace
        :math:x_i` is dvided by its corresponding MAD :math:`m_i`. This has
        the main effect to have the same level of background noise on each
        stream.

        """

        # Waitbar initialization
        waitbar = logtable.waitbar('Remove MAD', len(self))
        # Binarize
        for index, trace in enumerate(self):
            waitbar.progress(index)
            mad = robust.mad(trace.data)
            if mad > 0:
                trace.data /= mad
            else:
                trace.data /= (mad + 1e-5)

    def whiten(self, segment_duration_sec, method='onebit', smooth=11):
        """Spectral normalization of the traces.

        Parameters
        ----------
        segment_duration_sec : float
            Duration of the segments for Fourier transformation.

        Keyword arguments
        -----------------
        method : str
            ``"onebit"`` or ``"smooth"``. Wheter to consider the division with
            direct Fourier transform modulus, or a smooth version.

        smooth : int
            Smoothing window length in points.

        """

        # Define method
        if method == 'onebit':
            whiten_method = maths.phase
        elif method == 'smooth':
            whiten_method = maths.detrend_spectrum

        # Initialize for waitbar
        waitbar = logtable.waitbar('Whiten', len(self))
        fft_size = int(segment_duration_sec * self[0].stats.sampling_rate)
        duration = self[0].times()[-1]

        # Whiten
        for index, trace in enumerate(self):
            waitbar.progress(index)
            data = trace.data
            _, _, data_fft = stft(data, nperseg=fft_size)
            data_fft = whiten_method(data_fft, smooth=smooth)
            _, data = istft(data_fft, nperseg=fft_size)
            trace.data = data

        # Trim
        self.cut(pad=True, fill_value=0, starttime=self[0].stats.starttime,
                 endtime=self[0].stats.starttime + duration)

    def show(self, ax=None, scale=.5, index=0, ytick_size=6, **kwargs):
        """ Plot all seismic traces.

        The date axis is automatically defined with Matplotlib's numerical
        dates.

        Keyword arguments
        -----------------
        ax : :class:`matplotlib.axes.Axes`
            Previously instanciated axes. Default to None, and the axes are
            created.

        scale : float
            Scaling factor for trace amplitude.

        ytick_size : int
            The size of station codes on the left axis. Default 6 pts.

        kwargs : dict
            Other keyword arguments passed to
            :func:`matplotlib.pyplot.plot`.

        Return
        ------
        :class:`matplotlib.axes.Axes`
            The axes where the traces have been plotted.

        """

        # Parameters
        # ----------

        # Default parameters
        times = self.times
        kwargs.setdefault('rasterized', True)

        # Axes
        if ax is None:
            _, ax = plt.subplots(1, figsize=(7, 6))

        # Preprocess
        # ----------

        # Turn into array and normalize by multiple of max MAD
        traces = np.array([s.data for s in self])
        traces = traces / traces.max()
        if robust.mad(traces).max() > 0:
            traces /= robust.mad(traces).max()
        traces[np.isnan(traces)] = .0
        traces *= scale

        # Display
        # -------

        # Plot traces
        for index, trace in enumerate(traces):
            ax.plot(times, trace + index + 1, **kwargs)

        # Show station codes as yticks
        yticks = [' '] + [s.stats.station for s in self] + [' ']
        ax.set_yticks(range(len(self) + 2))
        ax.set_ylim([0, len(self) + 1])
        ax.set_ylabel('Seismic station code')
        ax.set_yticklabels(yticks, size=6)

        # Time axis
        ax.set_xlim(times[0], times[-1] + (times[2] - times[0]) / 2)
        xticks = md.AutoDateLocator()
        ax.xaxis.set_major_locator(xticks)
        ax.xaxis.set_major_formatter(md.AutoDateFormatter(xticks))

        return ax

    def stft(self, segment_duration_sec, bandwidth=None, step=0.5,
             **kwargs):
        """Short-time Fourier transform onto individual traces.

        This method makes use of the :func:`scipy.signal.stft` function to
        calculate the short-time Fourier transform of each seismic trace.

        Parameters
        ----------

        segment_duration_sec : float
            Duration of the short-time segments.

        Keyword arguments
        -----------------

        bandwidth : list
            Frequency limits onto which the spectra could be truncated.
            Default to None, i.e. all frequencies are kept.

        step : float
            Overlap between time segments. Default to 0.5.

        kwargs : dict
            Other kwargs passed to :func:`scipy.signal.stft`.

        Returns
        -------
        spectra : :class:`numpy.ndarray`
            The Fourier spectra of shape
            ``(n_stations, n_frequencies, n_times)``.

        frequencies : :class:`numpy.ndarray`
            The frequency axis (n_frequencies).

        times : :class:`numpy.ndarray`
            The starting time of each window in Matplotlib's date flaot format.

        """

        # Parameters
        # ----------

        # Defaults arguments
        kwargs.setdefault('fs', self[0].stats.sampling_rate)
        kwargs.setdefault('nperseg', int(segment_duration_sec * kwargs['fs']))
        kwargs.setdefault('noverlap', int(kwargs['nperseg'] * step))
        kwargs.setdefault('nfft', int(2**np.ceil(np.log2(kwargs['nperseg']))))
        kwargs.setdefault('window', 'hanning')
        kwargs.setdefault('return_onesided', False)
        kwargs.setdefault('boundary', None)
        kwargs.setdefault('padded', False)

        # Calculate spectra of each trace
        spectra = list()
        waitbar = logtable.waitbar('Spectra', len(self))
        for trace_id, trace in enumerate(self):
            waitbar.progress(trace_id)
            frequencies, times, spectrum = stft(trace.data, **kwargs)
            spectra.append(spectrum)

        # Reduces a list of spectra to an array
        spectra = np.array(spectra)

        # Calculate starting times of windows
        times -= times[0]
        times /= 24 * 3600
        times += md.date2num(self[0].stats.starttime.datetime)

        # Times are extended with last time of traces
        t_end = self.times[-1] + (self.times[2] - self.times[0]) / 2
        times = np.hstack((times, t_end))

        return times, frequencies, spectra


def show_spectrogram(times, frequencies, spectrum, ax=None, cax=None,
                     flim=None, step=.5, figsize=(6, 5), **kwargs):
    """Pcolormesh the spectrogram of a single seismic trace.

    The spectrogram (modulus of the short-time Fourier transform) is
    extracted from the complex spectrogram previously calculated from
    the :meth:`arrayprocessing.data.stft` method.

    The spectrogram is represented in log-scale amplitude normalized by
    the maximal amplitude (dB re max).

    The date axis is automatically defined with Matplotlib's dates.

    Parameters
    ----------

    times : :class:`np.ndarray`
        The starting times of the windows

    frequencies : :class:`np.ndarray`
        The frequency vector.

    spectra : :class:`np.ndarray`
        The spectrogram matrix of shape ``(n_station, n_frequencies, n_times)``

    Keyword arguments
    -----------------

    code : int or str
        Index or code of the seismic station.

    step : float
        The step between windows in fraction of segment duration.
        By default, assumes a step of .5 meaning 50% of overlap.

    ax : :class:`matplotlib.axes.Axes`
        Previously instanciated axes. Default to None, and the axes are
        created.

    cax : :class:`matplotlib.axes.Axes`
        Axes for the colorbar. Default to None, and the axes are created.
        These axes should be given if ``ax`` is not None.

    kwargs : dict
        Other keyword arguments passed to
        :func:`matplotlib.pyplot.pcolormesh`

    Return
    ------

        If the path_figure kwargs is set to None (default), the following
        objects are returned:

        fig (matplotlib.pyplot.Figure) the figure instance.
        ax (matplotlib.pyplot.Axes) axes of the spectrogram.
        cax (matplotlib.pyplot.Axes) axes of the colorbar.

    """

    # Axes
    if ax is None:
        gs = dict(width_ratios=[50, 1])
        fig, (ax, cax) = plt.subplots(1, 2, figsize=figsize, gridspec_kw=gs)

    # Safe
    spectrum = np.squeeze(spectrum)

    # Spectrogram
    spectrum = np.log10(np.abs(spectrum) / np.abs(spectrum).max())

    # Image
    kwargs.setdefault('rasterized', True)
    img = ax.pcolormesh(times, frequencies, spectrum, **kwargs)

    # Colorbar
    plt.colorbar(img, cax=cax)
    cax.set_ylabel('Spectral amplitude (dB re max)')

    # Date ticks
    ax.set_xlim(times[[0, -1]])
    xticks = md.AutoDateLocator()
    ax.xaxis.set_major_locator(xticks)
    ax.xaxis.set_major_formatter(md.AutoDateFormatter(xticks))

    # Frequencies
    ax.set_yscale('log')
    ax.set_ylabel('Frequency (Hz)')
    ax.set_ylim(frequencies[[1, -1]])

    return ax, cax
