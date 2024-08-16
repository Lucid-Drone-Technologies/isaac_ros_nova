from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch.substitutions import LaunchConfiguration

def generate_launch_description():
    # Declare common launch arguments (if needed globally)
    declare_throttle_type = DeclareLaunchArgument(
        'throttle_type',
        default_value='messages',
        description='Type of throttle (e.g., messages, bytes)'
    )
    declare_msgs_per_sec = DeclareLaunchArgument(
        'msgs_per_sec',
        default_value='5.0',
        description='Throttle rate in messages per second'
    )

    # Node configurations for different topics
    # front camera
    throttle_front_depth_info = Node(
        package='topic_tools',
        executable='throttle',
        name='throttle_front_depth_raw',
        output='screen',
        arguments=[
            LaunchConfiguration('throttle_type'),
            'front_stereo_camera/depth/camera_info',
            LaunchConfiguration('msgs_per_sec'),
            'front_stereo_camera/depth/camera_info_throttled'
        ]
    )

    throttle_front_depth_raw = Node(
        package='topic_tools',
        executable='throttle',
        name='throttle_front_depth_raw',
        output='screen',
        arguments=[
            LaunchConfiguration('throttle_type'),
            'front_stereo_camera/depth/image_raw',
            LaunchConfiguration('msgs_per_sec'),
            'front_stereo_camera/depth/image_raw_throttled'
        ]
    )

    throttle_front_left_info = Node(
        package='topic_tools',
        executable='throttle',
        name='throttle_front_left_info',
        output='screen',
        arguments=[
            LaunchConfiguration('throttle_type'),
            'front_stereo_camera/left/camera_info',
            LaunchConfiguration('msgs_per_sec'),
            'front_stereo_camera/left/camera_info_throttled'
        ]
    )

    throttle_front_left_raw = Node(
        package='topic_tools',
        executable='throttle',
        name='throttle_front_left_raw',
        output='screen',
        arguments=[
            LaunchConfiguration('throttle_type'),
            'front_stereo_camera/left/image_raw',
            LaunchConfiguration('msgs_per_sec'),
            'front_stereo_camera/left/image_raw_throttled'
        ]
    )


    throttle_front_right_info = Node(
        package='topic_tools',
        executable='throttle',
        name='throttle_front_right_info',
        output='screen',
        arguments=[
            LaunchConfiguration('throttle_type'),
            'front_stereo_camera/right/camera_info',
            LaunchConfiguration('msgs_per_sec'),
            'front_stereo_camera/right/camera_info_throttled'
        ]
    )

    throttle_front_right_raw = Node(
        package='topic_tools',
        executable='throttle',
        name='throttle_front_right_raw',
        output='screen',
        arguments=[
            LaunchConfiguration('throttle_type'),
            'front_stereo_camera/right/image_raw',
            LaunchConfiguration('msgs_per_sec'),
            'front_stereo_camera/right/image_raw_throttled'
        ]
    )

    return LaunchDescription([
        declare_throttle_type,
        declare_msgs_per_sec,
        throttle_front_depth_info,
        throttle_front_depth_raw,
        throttle_front_left_raw,
        throttle_front_left_info,
        throttle_front_right_info,
        throttle_front_right_raw
    ])