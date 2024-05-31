# SPDX-FileCopyrightText: NVIDIA CORPORATION & AFFILIATES
# Copyright (c) 2021-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

import io
import os

import matplotlib.pyplot as plt
import nav_msgs
import nav_msgs.msg
import numpy as np
import pandas as pd
from rclpy.serialization import deserialize_message
import rosbag2_py
from rosbags.rosbag2 import Reader
from rosbags.serde import deserialize_cdr
from rosidl_runtime_py.utilities import get_message
import sensor_msgs
import sensor_msgs.msg

VERBOSE_DUMP = 5
VERBOSE_INFO = 4
VERBOSE_WARNING = 3
VERBOSE_COMPACT = 2
VERBOSE_ERROR = 1

NUM_BINS = 64
VERBOSITY_MAP = {
    'dump': VERBOSE_DUMP,
    'error': VERBOSE_ERROR,
    'info': VERBOSE_INFO,
    'compact': VERBOSE_COMPACT,
    'warning': VERBOSE_WARNING,
}


def read_rosbag(input_file: str, verbose=VERBOSE_WARNING, store_data=False, bagtype='mcap'):
    """
    Read an arbitrary ROSbag into a dictionary of pandas data frames.

    Args
    ----
        input_file (str): The path to the rosbag file to be read.
        verbose (int, optional): Verbosity level. Defaults to VERBOSE_WARNING.
        store_data (bool, optional): Flag to indicate whether to store data in memory.
        bagtype(str, optional): Flag indicating bag extensions, options are mcap and db3

    Returns
    -------
        {str: pd.DataFrame} : A dictionary mapping topic names to a DataFrame containing the
            extracted data.

    """

    def _read_mcap_file(mcapfile: str, store_data=False):
        # Reads an mcap file, we can actually use this for db3 files as well but for some reason
        # it is much slower
        data_by_topic = {}
        fails_by_topic = {}

        reader = rosbag2_py.SequentialReader()
        reader.open(
            rosbag2_py.StorageOptions(uri=mcapfile, storage_id='mcap'),
            rosbag2_py.ConverterOptions(
                input_serialization_format='cdr', output_serialization_format='cdr'
            ),
        )

        topic_types = reader.get_all_topics_and_types()

        def typename(topic_name):
            for topic_type in topic_types:
                if topic_type.name == topic_name:
                    return topic_type.type
            raise ValueError(f'topic {topic_name} not in bag')

        while reader.has_next():
            topic, data, timestamp = reader.read_next()
            try:
                msg_type = get_message(typename(topic))
                # TODO sgillen - this is the bottleneck, for quick tests we don't actually need
                # The full message, just the timestamp, but at least for now we still parse the
                # whole thing. There are some short term gains we can get for free, like just
                # distributing this over X cores ...
                msg = deserialize_message(data, msg_type)
            except Exception as e:
                if topic not in fails_by_topic:
                    fails_by_topic[topic] = True
                    if verbose >= VERBOSE_ERROR:
                        print(f'Error deserializing {topic}: {e}. Skipping.')
                continue

            if hasattr(msg, 'header'):
                if topic not in data_by_topic:
                    data_by_topic[topic] = {'timestamps': [], 'data': [], 'acqtime': []}
                    data_by_topic[topic]['data_type'] = type(msg)
                data_by_topic[topic]['timestamps'].append(timestamp)

                # TODO (sgillen) if we need to eventually work with larger (10s++ of GB files)
                # we may need to look into replacing pandas with dask.
                if store_data:
                    data_by_topic[topic]['data'].append(msg)
                if hasattr(msg.header, 'stamp'):
                    acqtime = msg.header.stamp.nanosec + msg.header.stamp.sec * 1e9
                    data_by_topic[topic]['acqtime'].append(acqtime)
                else:
                    data_by_topic[topic]['acqtime'].append(None)
            else:
                pass
                # print(f'{topic} has no header')

        del reader
        return data_by_topic, fails_by_topic

    def _read_db3_file(db3_dir: str, store_data=False):
        # Read a db3 rosbag in
        data_by_topic = {}
        fails_by_topic = {}

        with Reader(db3_dir) as reader:
            # Iterate over messages
            for connection, timestamp, rawdata in reader.messages():
                topic = connection.topic
                try:
                    msg = deserialize_cdr(rawdata, connection.msgtype)
                except Exception as e:
                    if topic not in fails_by_topic:
                        fails_by_topic[topic] = True
                        print(f'Error deserializing {topic}: {e}. Skipping.')
                    continue

                if hasattr(msg, 'header'):
                    if topic not in data_by_topic:
                        data_by_topic[topic] = {'timestamps': [], 'data': [], 'acqtime': []}
                        data_by_topic[topic]['data_type'] = type(msg)
                    data_by_topic[topic]['timestamps'].append(timestamp)

                    # TODO (sgillen) if we need to eventually work with larger (10s++ of GB files)
                    # we may need to look into replacing pandas with dask, or find some other way
                    if store_data:
                        data_by_topic[topic]['data'].append(msg)
                    if hasattr(msg.header, 'stamp'):
                        acqtime = msg.header.stamp.nanosec + msg.header.stamp.sec * 1e9
                        data_by_topic[topic]['acqtime'].append(acqtime)
                    else:
                        data_by_topic[topic]['acqtime'].append(None)
                else:
                    pass
                    print(f'{topic} has no header')
        del reader
        return data_by_topic, fails_by_topic

    if not os.path.exists(input_file) and not os.path.isdir(input_file):
        raise FileNotFoundError(f'The specified bag file does not exist: {input_file}')
    try:
        if bagtype == 'mcap':
            data_by_topic, fails_by_topic = _read_mcap_file(input_file, store_data=store_data)
        elif bagtype == 'db3':
            data_by_topic, fails_by_topic = _read_db3_file(input_file, store_data=store_data)
        else:
            raise NotImplementedError(
                f'Unsupported bag format {bagtype}, supported options are db3 and mcap'
            )
    except BaseException:
        print('Error opening bagfile, checking a few things...')
        # Check if the ROS MCAP storage APT package is installed
        try:
            ros_distro = os.environ['ROS_DISTRO']
            import subprocess

            subprocess.run(
                ['dpkg', '-s', f'ros-{ros_distro}-rosbag2-storage-mcap'],
                check=True,
                capture_output=True,
            )

            print('You have mcap installed .., ')
            raise
        except KeyError:
            print('The ROS_DISTRO environment variable is not set.')
            print(
                'Suggestion: Ensure that ROS is properly installed and source the setup script,'
                'e.g., "source /opt/ros/<distro>/setup.bash"'
            )
            raise EnvironmentError('ROS_DISTRO environment variable is not set.')
    except subprocess.CalledProcessError:
        print('The ROS MCAP storage APT package is not installed or not found.')
        print(
            f'Suggestion: Install the package using the command:'
            f'sudo apt install ros-{ros_distro}-rosbag2-storage-mcap'
        )
        raise

    dfs = {}
    for topic, values in data_by_topic.items():
        dfs[topic] = pd.DataFrame(
            {
                'timestamp': values['timestamps'],
                'acqtime': values['acqtime'],
            }
        )
        dfs[topic].data_type = type(values['data_type'])
        if store_data:
            dfs[topic]['data'] = values['data']

    if verbose >= VERBOSE_INFO:
        print(f'Found the following topics in file {input_file}')
        for topic, values in dfs.items():
            print(f'{topic}: type: {values.data_type} count: {len(values["acqtime"])}')

    return dfs


def do_validation(input_file, verbose=VERBOSE_WARNING, title=None):
    """
    Validate a single bag file.

    This will run all tests, and then print the results to the console

    Args
    ----
        input_file (str): The bag file to analyze
        verbose (int): The verbosity level
        title (str): Optional, The title for the report

    Returns
    -------
        stats: A dictionary of statistics from the tests
        errors: A dictionary of errors from the tests
        dfs: A dictionary of dataframes used for the tests

    """
    dfs = read_rosbag(input_file, verbose=verbose)
    all_stats, all_errors = _analyze_single(dfs, verbose=verbose)

    if title is None:
        title = input_file.split('/')[-1]
    q_scores = _summarize(all_stats, all_errors, dfs, title, verbose)

    return all_stats, all_errors, dfs, q_scores


def _summarize(all_stats, all_errors, dfs, title, verbose=VERBOSE_WARNING):
    # Summarize a single bag file, takes in errors and stats and prints a nice report about them
    if len(all_errors) == 0:
        print('Warning! No cameras found!')

    LINE_LENGTH = NUM_BINS + 50
    padding_length = (LINE_LENGTH - len(title) - 2) // 2
    title_bar = '=' * padding_length + f' {title} ' + '=' * padding_length

    # Adjust if the total length is off by one due to odd number
    if len(title) < LINE_LENGTH:
        title += '='

    output_buffer = io.StringIO()
    table_buffer = io.StringIO()
    sync_buffer = io.StringIO()

    drop_tables = []
    sync_tables = []
    all_drops = []
    all_captures = []

    for sensor_key, errors in sorted(all_errors.items()):
        sensor_key_short = '/'.join(sensor_key.split('/')[:3])
        stats = all_stats[sensor_key]
        if 'image_compressed' in sensor_key and 'sync' not in sensor_key:
            all_drops.append(stats['num_frames_dropped'])
            all_captures.append(stats['total_frames_captured'])
            drop_tables.append(stats['ascii_drop_table'])
            print(f'{sensor_key_short:<42} [{stats["ascii_drop_table"]}]', file=table_buffer)
            print(
                f'{sensor_key_short}:\n'
                f'    - Percent Dropped: {stats["percent_frames_dropped"]}%\n'
                f'    - Number Dropped: {stats["num_frames_dropped"]}\n'
                f'    - Mean Frequency: {stats["mean_frequency_all"]}\n'
                f'    - Drop Table: [{stats["ascii_drop_table"]}]\n'
                f'    - Frames Captured: {stats["total_frames_captured"]}\n'
                f'    - Total Jitter: {stats["mean_absolute_error_all_ms"]} ms\n'
                f'    - Jitter Excluding Drops: {stats["mean_absolute_error_filtered_ms"]} ms\n'
                f'    - Largest Frame Drop: {stats["largest_drop"]} ms\n',
                file=output_buffer,
            )

        elif 'sync' in sensor_key and 'inter' not in sensor_key:
            print(f'{sensor_key_short:<42} [{stats["ascii_table"]}]', file=sync_buffer)
            sync_tables.append(stats['ascii_table'])

            print(
                f'{sensor_key}:\n'
                f'    - Num Desyncs : {stats["num_desynced_frames"]}\n'
                f'    - Percent Desynced: {stats["percent_desynced_frames"]}\n'
                f'    - Mean Difference : {stats["average_difference_ns"]}\n'
                f'    - Max Difference: {[stats["max_diff"]]}\n'
                f'    - Desync Table: {[stats["ascii_table"]]}\n',
                file=output_buffer,
            )

        elif 'stereo_imu' in sensor_key:
            print(
                f'{sensor_key_short}:\n'
                f'    - Percent Dropped: {stats["percent_indices_dropped"]}%\n'
                f'    - Frames Dropped: {stats["num_indices_dropped"]}\n'
                f'    - Mean Frequency: {stats["mean_frequency_all"]}\n'
                f'    - Drop Table: [{stats["ascii_drop_table"]}]\n'
                f'    - Frames Captured: {stats["total_frames_captured"]}\n'
                f'    - Total Jitter: {stats["mean_absolute_error_all_ms"]} ms\n'
                f'    - Jitter Excluding Drops: {stats["mean_absolute_error_filtered_ms"]} ms\n',
                file=output_buffer,
            )
        elif sensor_key in (
            '/odom',
            '/battery_state',
            '/imu',
            '/chassis/odom',
            '/chassis/battery_state',
            '/chassis/imu',
        ):
            print(
                f'{sensor_key_short}:\n'
                f'    - Percent Dropped/High-Jitter: {stats["percent_indices_dropped"]}%\n'
                f'    - Number Dropped/High-Jitter: {stats["num_indices_dropped"]}\n'
                f'    - Mean Frequency: {stats["mean_frequency_all"]}\n'
                f'    - Drop / Jitter Table: [{stats["ascii_drop_table"]}]\n'
                f'    - Frames Captured: {stats["total_frames_captured"]}\n'
                f'    - Total Jitter: {stats["mean_absolute_error_all_ms"]} ms\n'
                f'    - Jitter Excluding Drops: {stats["mean_absolute_error_filtered_ms"]} ms\n',
                file=output_buffer,
            )

    def _calculate_bucket_kpi(all_tables):
        # Compute the intersection over a list of tables
        final_string = '.' * NUM_BINS
        fail_buckets = 0

        for i in range(NUM_BINS):
            for table in all_tables:
                if table[i] == 'x':
                    final_string = final_string[:i] + 'x' + final_string[i + 1:]
                    fail_buckets += 1
                    break

        return fail_buckets, final_string

    try:
        drop_buckets, drop_table_string = _calculate_bucket_kpi(drop_tables)
    except Exception as e:
        drop_buckets = e
        drop_table_string = e

    try:
        sync_buckets, sync_table_string = _calculate_bucket_kpi(sync_tables)
    except Exception as e:
        sync_buckets = e
        sync_table_string = e

    try:
        qscore_buckets = 100 * (1 - (drop_buckets / NUM_BINS))
        qscore_buckets = f'{qscore_buckets:.1f}'
    except Exception as e:
        qscore_buckets = e

    try:
        qscore_intra_sync = 100 * (1 - (sync_buckets / NUM_BINS))
        qscore_intra_sync = f'{qscore_intra_sync:.1f}'
    except Exception as e:
        qscore_intra_sync = e

    try:
        qscore_inter_sync = (100 - (all_stats['inter_camera_sync']['percent_desynced_frames']))
        qscore_inter_sync = f'{qscore_inter_sync:.1f}'
    except Exception as e:
        qscore_intra_sync = e

    try:
        qscore_drops = 100 * (1 - sum(all_drops) / (sum(all_captures) + sum(all_drops)))
        qscore_drops = f'{qscore_drops:.1f}'
    except Exception as e:
        qscore_drops = e

    q_scores = {
        'qscore_buckets': qscore_buckets,
        'qscore_intra_sync': qscore_intra_sync,
        'qscore_inter_sync': qscore_inter_sync,
        'qscore_drops': qscore_drops
    }

    # TODO should probably add verbose = SILENT
    print(title_bar)

    print(f'Camera Drop Q Score: {qscore_drops}')

    if (len(drop_tables)):
        print(f'Camera Bucket Q Score: {qscore_buckets}')
    else:
        print('Camera Bucket Q Score: N/A')

    if (len(sync_tables)):
        print(f'Intra Camera Sync Q score: {qscore_intra_sync}')
    else:
        print('Intra Camera Sync Q score: N/A')

    print('\n')

    if (len(drop_tables)):
        print(f'{"Camera Drop Table":<42} [{drop_table_string}]')
        print(table_buffer.getvalue())
    else:
        print('No Camera Drop Table Found')

    if len(sync_tables):
        print(f'{"Intra Camera Sync Table":<42} [{sync_table_string}]')
        print(sync_buffer.getvalue())
        print()
    else:
        print('No Camera Sync Table Found')

    print('\n')

    for topic, errors in sorted(all_errors.items()):
        if 'duplicate_ts' in errors and errors['duplicate_ts']['num_errors'] > 0:
            print(f'Warning: {topic} '
                  f'has {errors["duplicate_ts"]["num_errors"]} duplicate timestamps')
        if 'backwards_timestamp' in errors and errors['backwards_timestamp']['num_errors'] > 0:
            print(f'Warning: {topic} '
                  f'has {errors["backwards_timestamp"]["num_errors"]} backwards timestamps')
        if 'large_drop' in errors and errors['large_drop']['num_errors'] > 0:
            print(f'Warning: {topic} has {errors["large_drop"]["num_errors"]} large drops, '
                  f'greatest was {max(errors["large_drop"]["acqtimes"]) / 1e6} ms')

    print('\n')

    print('Topics:')
    for topic in dfs.keys():
        try:
            print(f'    {topic} | frames_captured: {len(dfs[topic])}')
        except Exception as e:
            print(f'    {topic} | frames_captured: {e}')

    print('\n')

    if verbose >= VERBOSE_WARNING:
        print(output_buffer.getvalue())

    return q_scores


def _analyze_single(dfs, verbose=VERBOSE_WARNING):
    # Analyzes a single bag file
    bag_tester = BagTester(dfs, verbose=verbose)

    # TODO at least add the ability to override specific topic names
    # test_config = {
    #     "/front_stereo_camera/imu": (30.0, 0.01),
    # }

    test_config = {
        'camera_acqtime': {
            sensor_msgs.msg._compressed_image.CompressedImage: (30.0, 0.01),
            'max_drops_in_a_row': 2,
        },
        'imu_acqtime': {
            sensor_msgs.msg._imu.Imu: (100.0, 0.02),
            sensor_msgs.msg._imu.Metaclass_Imu: (100.0, 0.02),
        },
        'segway_acqtime': {
            sensor_msgs.msg._imu.Imu: (40.0, 0.5),
            sensor_msgs.msg._imu.Metaclass_Imu: (40.0, 0.5),
            nav_msgs.msg._odometry.Odometry: (40.0, 0.5),
            nav_msgs.msg._odometry.Metaclass_Odometry: (40.0, 0.5),
            sensor_msgs.msg._battery_state.BatteryState: (100.0, 0.5),
            sensor_msgs.msg._battery_state.Metaclass_BatteryState: (100.0, 0.5),
        },
        'intra_cam_sync': {
            # 'sync_tolerance_ns': 20000.0,  # 20us
            'sync_tolerance_ns': 0.0,  # exact match
        },
        'inter_cam_sync': {
            'sync_tolerance_ns': 150000.0,  # 150us
            'nominal_frequency': (30.0),
        }
    }

    camera_topics = [
        topic
        for topic in bag_tester.dfs.keys()
        if 'camera' in topic or 'owl' in topic or 'hawk' in topic
    ]

    imu_topics = [
        topic
        for topic in bag_tester.dfs.keys()
        if 'stereo_imu' in topic
    ]

    segway_topics = [
        '/odom',
        '/battery_state',
        '/imu',
        '/chassis/odom',
        '/chassis/battery_state',
        '/chassis/imu'
    ]

    camera_stats, camera_errors = bag_tester.analyze_acquisition_time(
        camera_topics, test_config['camera_acqtime'], show_error_plots=False)

    sync_stats, sync_errors = bag_tester.check_stereo_sync(
        camera_topics, test_config['intra_cam_sync'])

    multi_sync_stats, multi_sync_errors = bag_tester.check_multi_sync(
        camera_topics, test_config['inter_cam_sync'])

    imu_stats, imu_errors = bag_tester.analyze_acquisition_time(
        imu_topics, test_config['imu_acqtime'], show_error_plots=False)

    segway_stats, segway_errors = bag_tester.analyze_acquisition_time(
        segway_topics, test_config['intra_cam_sync'], show_error_plots=False
    )

    # TODO this should probably be in summarize above, but making the topic lists
    # would then need to be replicated ...
    if verbose >= VERBOSE_INFO:
        print('\n------------------ Camera Data ------------------')
        _pretty_print(camera_stats, print_index=(verbose >= VERBOSE_DUMP))
        _pretty_print(imu_stats, print_index=(verbose >= VERBOSE_DUMP))

        print('\n------------------ Segway Data ------------------')
        _pretty_print(segway_stats, print_index=(verbose >= VERBOSE_DUMP))

    all_errors = {**camera_errors, **sync_errors, **multi_sync_errors, **segway_errors}
    all_stats = {**camera_stats, **sync_stats, **multi_sync_stats, **segway_stats}
    return all_stats, all_errors


def _pretty_print(stats, print_index=False):
    # Pretty(ish) print function for stats
    for name, stats in stats.items():
        print(f'{name}: ')
        for k, s in stats.items():
            if not print_index and (k == 'indices_dropped' or k == 'timestamps_dropped'):
                pass
            else:
                print(f'{k}: {s}')
        print()


def create_ascii_table(all_timestamps, bad_indices, total_slots=NUM_BINS):
    """
    Create an ascii table showing bad indices.

    'Bad indices' is generic, it could be indices where a drop was detected
    where a value was out of range, etc etc. in general there will be more
    timestamps than slots, in this case each slot is marked as bad if it
    contains at least one bad sample.

    Args
    ----
        all_timestamps: A list of all timestamps in the bag file.
        bad_indices: A list of all bad indices.
        total_slots: Total number of slots in the table

    Returns
    -------
        A string representing the ascii table.

    """
    min_index = 0
    max_index = len(all_timestamps) - 1
    slot_width = (max_index - min_index) / total_slots

    slots = np.zeros(total_slots, dtype=int)
    for index in bad_indices:
        slot_index = int((index - min_index) / slot_width)
        slot_index = min(slot_index, total_slots - 1)
        slots[slot_index] = 1

    ascii_table = ''.join(['x' if slot else '.' for slot in slots])
    return ascii_table


class BagTester:
    """Helper for running automated tests on a bag file."""

    def __init__(self, dfs, plot_dir=None, verbose=VERBOSE_WARNING):
        """
        Initialize a BagTester.

        dfs: dictionary of dataframes from read_rosbag
        plot_dir: directory to save any plots to
        verbose: verbosity level

        """
        self.dfs = dfs
        self.plot_dir = '/tmp/monitor_out/'

    def analyze_acquisition_time(self, topics, test_config, **kwargs):
        """
        Analyze acquisition time for a list of topics.

        Will check acqtimes for drops and other problems

        Args
        ----
            topics: list of topics to analyze
            test_config: dict of test configs

        Returns
        -------
            stats: dictionary of {str: dict} containing stats for a topic
            errors: dictionary of {str: list} containing errors for a topic

        """
        if isinstance(topics, str):
            topics = [topics]

        all_stats = {}
        all_errors = {}
        for i, topic in enumerate(topics):
            if topic not in self.dfs:
                continue

            stats, errors = self._analyze_acquisition_time(
                topic, test_config, **kwargs)
            all_stats[topic] = stats
            all_errors[topic] = errors

        return all_stats, all_errors

    def _analyze_acquisition_time(self,
                                  topic,
                                  test_config,
                                  show_all_plots=False,
                                  show_error_plots=False,
                                  use_imu_hack=True):
        # analyze_acquisition_time but for a single topic

        if topic not in self.dfs:
            print(f'Topic "{topic}" not found in the data.')
            return

        message_type = self.dfs[topic].data_type

        if 'stereo_imu' in topic and use_imu_hack:
            try:
                # self.dfs[topic + '_nohack'] = self.dfs[topic].copy(deep=True)
                self.dfs[topic] = self.dfs[topic].iloc[32:]
            except Exception as e:
                print(e)
                pass

        nominal_freq, tol = test_config.get(message_type, (30.0, 0.5))

        assert nominal_freq > 0
        nominal_period_ns = (1 / nominal_freq) * 1e9
        threshold_ns = tol * nominal_period_ns

        acqtime_diffs = self.dfs[topic]['acqtime'].diff()
        abs_diff_from_nominal = (acqtime_diffs - nominal_period_ns).abs()

        # Find indices where the absolute difference exceeds the threshold
        indices_to_remove = abs_diff_from_nominal[abs_diff_from_nominal >
                                                  threshold_ns].index
        timestamps_removed = self.dfs[topic]['acqtime'][indices_to_remove]

        # Account for several frames being dropped in a row
        num_frames_dropped = 0
        for diff in abs_diff_from_nominal:
            try:
                if diff > threshold_ns:
                    num_frames_dropped += max(int(diff / nominal_period_ns), 1)
            except ValueError:
                # Ignore NaNs
                pass

        if num_frames_dropped < len(indices_to_remove):
            print(
                f'Warning: Removing more samples than detected drops for topic {topic},'
                f'Likely your drop threshold is too strict')

        # Calculate the percentage of frames dropped
        total_samples = len(self.dfs[topic])

        try:
            percent_frames_dropped = (num_frames_dropped /
                                      (total_samples + num_frames_dropped)) * 100
        except Exception as e:
            percent_frames_dropped = e

        try:
            percent_indices_dropped = len(indices_to_remove) / len(self.dfs[topic]),
        except Exception as e:
            percent_indices_dropped = e

        # Remove these samples from the DataFrame
        filtered_acqtime_diffs = acqtime_diffs.drop(indices_to_remove)

        decreasing_ts = acqtime_diffs[acqtime_diffs < 0]
        duplicate_ts = acqtime_diffs[acqtime_diffs == 0]

        # Check for large drops
        startup_time_ns = 10 * 1e9
        start_time = self.dfs[topic]['acqtime'].min() + startup_time_ns
        end_time = self.dfs[topic]['acqtime'].max() - startup_time_ns

        max_drops_in_a_row = test_config.get('max_drops_in_a_row', -1)
        large_drop_thresh = ((max_drops_in_a_row + 1) * nominal_period_ns) - threshold_ns

        large_drops = acqtime_diffs[(acqtime_diffs > large_drop_thresh) & (
            self.dfs[topic]['acqtime'] > start_time) & (self.dfs[topic]['acqtime'] < end_time)]

        ascii_table = create_ascii_table(acqtime_diffs, indices_to_remove)

        stats = {
            'total_frames_captured': len(self.dfs[topic]),
            'num_frames_dropped': num_frames_dropped,
            'largest_drop': acqtime_diffs.max() / 1e6,
            'largest_drop_no_startup': large_drops.max() / 1e6,
            'mean_frequency_all': 1e9 / acqtime_diffs.mean(),  # Frequency in Hz
            'mean_frequency_filtered': 1e9 / filtered_acqtime_diffs.mean(),  # Frequency in Hz
            'nominal_frequency': nominal_freq,
            'drop threshold': tol,
            'ascii_drop_table': ascii_table,  # Filled in plot code below
            'indices_dropped': indices_to_remove.to_list(),
            'timestamps_dropped': timestamps_removed.to_list(),
            'num_indices_dropped': len(indices_to_remove),
            'percent_indices_dropped': percent_indices_dropped,
            'percent_frames_dropped': percent_frames_dropped,
            'mean_absolute_error_all_ms': abs_diff_from_nominal.mean() / 1e6,
            'mean_absolute_error_filtered_ms':
            (filtered_acqtime_diffs - nominal_period_ns).abs().mean() / 1e6,
            'max_absolute_error_all_ms': abs_diff_from_nominal.max() / 1e6,
            'max_absolute_error_filtered_ms':
            (filtered_acqtime_diffs - nominal_period_ns).abs().max() / 1e6,
            'std_ms': acqtime_diffs.std() / 1e6,
            'std_filtered_ms': filtered_acqtime_diffs.std() / 1e6,
        }

        errors = {
            'frame_drop': {
                'num_errors': len(indices_to_remove.to_list()),
                'indices': indices_to_remove.to_list(),
                'acqtimes': timestamps_removed.to_list()
            },
            'large_drop': {
                'num_errors': len(large_drops),
                'indices': large_drops.index.to_list(),
                'acqtimes': large_drops.to_list()
            },
            'backwards_timestamp': {
                'num_errors': len(decreasing_ts),
                'indices': decreasing_ts.index.to_list(),
                'acqtimes': decreasing_ts.to_list()
            },
            'duplicate_timestamp': {
                'num_errors': len(duplicate_ts),
                'indices': duplicate_ts.index.to_list(),
                'acqtimes': duplicate_ts.to_list()
            }
        }

        # Convert the difference from nanoseconds to microseconds for plotting
        us_diff_all = (acqtime_diffs - acqtime_diffs.mean()) / 1e3
        fig, axs = plt.subplots(1, 2, figsize=(10, 4))
        axs[0].plot(us_diff_all)
        axs[0].set_ylabel('Difference (μs)')
        axs[0].set_xlabel('Sample Index')
        axs[0].set_title(f'Jitter {topic}', fontsize=10)
        axs[0].grid(True)
        axs[0].ticklabel_format(style='sci', axis='y', scilimits=(0, 0))

        # Convert the filtered difference from nanoseconds to microseconds
        us_diff_filtered = (filtered_acqtime_diffs -
                            filtered_acqtime_diffs.mean()) / 1e3
        axs[1].plot(us_diff_filtered)
        axs[1].set_ylabel('Difference (μs)')
        axs[1].set_xlabel('Sample Index')
        axs[1].set_title('no drop:', fontsize=10)
        axs[1].grid(True)
        axs[1].ticklabel_format(style='sci', axis='y', scilimits=(0, 0))

        plt.tight_layout()

        if self.plot_dir:
            if not os.path.exists(self.plot_dir):
                os.makedirs(self.plot_dir)

            safe_topic = topic.replace('/', '_')
            fig.savefig(
                os.path.join(self.plot_dir, f'{safe_topic}_analysis.png'))

        if show_all_plots or errors['frame_drop'][
                'num_errors'] and show_error_plots:
            plt.show()
        plt.close(fig)

        return stats, errors

    def check_stereo_sync(self, topics, test_config, **kwargs):
        """
        Check sync between left/right versions of a topic.

        Assumes topic names are of the form:
        /some/arbitrary/topic/left/type
        /some/arbitrary/topic/right/type

        For example:
        /front_stereo_camera/left/image_compressed
        /front_stereo_camera/right/image_compressed

        Args:
        ----
            topics (list): List of topics to check
            test_config (dict): Dictionary of test configuration

        Returns
        -------
            stats (dict): Dictionary of statistics
            errors (dict): Dictionary of errors

        """

        def pair_topics_with_multiple_types(topics):
            paired = []
            topics_dict = {}

            # Create a dictionary with key as the base topic
            for topic in topics:
                parts = topic.split('/')
                base_topic = '/'.join(parts[:-2])
                side = parts[-2]
                topic_type = parts[-1]
                if base_topic not in topics_dict:
                    topics_dict[base_topic] = {}
                if topic_type not in topics_dict[base_topic]:
                    topics_dict[base_topic][topic_type] = {
                        'left': None,
                        'right': None
                    }
                topics_dict[base_topic][topic_type][side] = topic

            # Pair the topics based on left and right parts for each type
            for base_topic, types in topics_dict.items():
                for topic_type, sides in types.items():
                    if sides['left'] and sides['right']:
                        paired.append((sides['left'], sides['right']))

            return paired

        paired_topics = pair_topics_with_multiple_types(topics)
        paired_topics

        all_stats = {}
        all_errors = {}
        for pair in paired_topics:
            stats, errors = self._check_stereo_sync(pair, test_config,
                                                    **kwargs)
            sync_name = ''.join(pair[0].split('left/')) + '/sync'
            all_stats[sync_name] = stats
            all_errors[sync_name] = errors

        return all_stats, all_errors

    def _check_stereo_sync(self, topics, test_config):
        acqtimes_0 = self.dfs[topics[0]]['acqtime']
        acqtimes_1 = self.dfs[topics[1]]['acqtime']
        sync_tolerance_ns = test_config['sync_tolerance_ns']

        if len(self.dfs[topics[0]]['acqtime']) > len(
                self.dfs[topics[1]]['acqtime']):
            acqtimes = self.dfs[topics[0]]['acqtime']
        else:
            acqtimes = self.dfs[topics[1]]['acqtime']

        differences = pd.Series(np.zeros(len(acqtimes_0)))
        for i, ts in enumerate(acqtimes_0):
            closest_index = np.abs(acqtimes_1 - ts).idxmin()
            difference = abs(ts - acqtimes_1[closest_index])
            differences[i] = difference

        desynced_ts = differences > sync_tolerance_ns

        try:
            ascii_table = create_ascii_table(acqtimes,
                                             desynced_ts.index[desynced_ts])
        except Exception as e:
            ascii_table = e

        num_desynced_frames = desynced_ts.sum()
        total_frames = len(desynced_ts)
        percent_desynced = (num_desynced_frames / total_frames) * 100
        average_diff = differences.mean()
        max_diff = differences.max()

        stats = {
            'indices_desynced': desynced_ts.index.to_list(),
            'timestamped_desynced': acqtimes[desynced_ts.index],
            'ascii_table': ascii_table,
            'num_desynced_frames': num_desynced_frames,
            'percent_desynced_frames': percent_desynced,
            'average_difference_ns': average_diff,
            'max_diff': max_diff,
        }

        errors = {
            'desync': {
                'num_errors': num_desynced_frames,
                'indices': desynced_ts.index.to_list(),
                'acqtimes': acqtimes[desynced_ts.index].to_list()
            }
        }
        return stats, errors

    def check_multi_sync(self, topics, test_config, **kwargs):
        """
        Check for sync between all camera streams.

        Args:
        ----
            topics: (list) List of topics to check
            test_config: (dict) Dictionary of test configuration

        Returns
        -------
            stats (dict): Dictionary of statistics
            errors (dict): Dictionary of errors


        """
        # Calculate differences matrix
        differences_matrix = {}
        for i, topic_i in enumerate(topics):
            for j, topic_j in enumerate(topics):
                if i < j:  # To avoid redundant calculations and self comparison
                    differences_matrix[(topic_i, topic_j)] = (
                        self.dfs[topic_i]['acqtime'] - self.dfs[topic_j]['acqtime']
                    ).abs()

        # Determine overall desync based on tolerance
        desynced_matrix = {
            topics_pair: differences > test_config['sync_tolerance_ns']
            for topics_pair, differences in differences_matrix.items()
        }

        # Find longest acqtime series for reference
        lengths = {topic: len(self.dfs[topic]['acqtime']) for topic in topics}
        longest_topic = max(lengths, key=lengths.get)
        acqtimes = self.dfs[longest_topic]['acqtime']

        offsets_to_longest = {}
        for topic in topics:
            acqtime_diff = self.dfs[topic]['acqtime'] - self.dfs[longest_topic]['acqtime']
            offsets_to_longest[topic] = np.round(acqtime_diff /
                                                 (1e9 / test_config['nominal_frequency']))

        # # Print offsets relative to the longest topic
        # print(f"Using topic: {longest_topic} as the reference")
        # for topic, offset_frames in offsets_to_longest.items():
        #     print(f"Offset of {topic} to {longest_topic}: {offset_frames:.2f} frames")

        # Consolidate desync information
        all_desynced_indices = set()
        for desynced in desynced_matrix.values():
            all_desynced_indices.update(desynced.index[desynced])

        # Include extra messages from the longest topic as desynced
        max_length = lengths[longest_topic]
        for topic in topics:
            if lengths[topic] < max_length:
                all_desynced_indices.update(range(lengths[topic], max_length))

        # Attempt to create ascii table (placeholder for actual function)
        try:
            ascii_table = create_ascii_table(acqtimes, list(all_desynced_indices))
        except Exception as e:
            ascii_table = e

        # Statistics
        num_desynced_frames = len(all_desynced_indices)
        total_frames = len(acqtimes)
        percent_desynced = (num_desynced_frames / total_frames) * 100
        average_diff = sum(d.mean() for d in differences_matrix.values()) / len(differences_matrix)
        max_diff = max(d.max() for d in differences_matrix.values())

        stats = {
            'inter_camera_sync': {
                'indices_desynced': list(all_desynced_indices),
                'timestamped_desynced': acqtimes[list(all_desynced_indices)],
                'ascii_table': ascii_table,
                'num_desynced_frames': num_desynced_frames,
                'percent_desynced_frames': percent_desynced,
                'average_difference_ns': average_diff,
                'max_diff': max_diff,
                'longest_topic': longest_topic,
                'offsets': offsets_to_longest
            }}

        errors = {
            'inter_camera_sync': {
                'desync': {
                    'num_errors': num_desynced_frames,
                    'indices': list(all_desynced_indices),
                    'acqtimes': acqtimes[list(all_desynced_indices)].to_list()
                }}}

        return stats, errors
