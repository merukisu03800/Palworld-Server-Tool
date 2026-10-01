import boto3
import json
import os
import time

ALLOWED_PROFILES = ['Brown', 'Drop', 'Oillig', 'Power', 'Origin']
PUT_SERVER_INIT_PATH = '/home/ubuntu/SettingTool/PutServerInit.sh'

def cors_headers():
    return {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*'
    }

def response(status_code, body_dict):
    return {
        'statusCode': status_code,
        'headers': cors_headers(),
        'body': json.dumps(body_dict)
    }

def wait_for_instance_running(ec2_client, instance_id, max_attempts=40):
    """インスタンスが完全に起動するまで待機"""
    for i in range(max_attempts):
        resp = ec2_client.describe_instances(InstanceIds=[instance_id])
        state = resp['Reservations'][0]['Instances'][0]['State']['Name']

        if state == 'running':
            return resp['Reservations'][0]['Instances'][0]

        if i < max_attempts - 1:  # 最後の試行を除いて待機
            time.sleep(3)  # 3秒待機

    raise Exception('Instance did not enter running state within the expected time')

def dispatch_command(ssm, instance_id, commands, message):
    send_result = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName='AWS-RunShellScript',
        Parameters={'commands': commands},
        TimeoutSeconds=120
    )
    command_id = send_result['Command']['CommandId']

    # API Gateway (REST API) のLambda統合タイムアウトは29秒固定のため、
    # ここでは完了を待たずにcommand_idを返し、ブラウザ側でポーリングさせる。
    return response(202, {
        'message': message,
        'command_id': command_id,
        'status': 'InProgress'
    })

def require_running_instance(ec2, instance_id):
    instance_state = ec2.describe_instances(InstanceIds=[instance_id])['Reservations'][0]['Instances'][0]['State']['Name']
    if instance_state != 'running':
        return response(400, {
            'error': f'Instance {instance_id} is not running (current state: {instance_state}). Start the server first.'
        })
    return None

def handle_apply_profile(ec2, ssm, instance_id, profile):
    if profile not in ALLOWED_PROFILES:
        return response(400, {
            'error': f'Invalid profile: {profile}. Valid profiles are: {", ".join(ALLOWED_PROFILES)}'
        })

    not_running = require_running_instance(ec2, instance_id)
    if not_running:
        return not_running

    return dispatch_command(
        ssm, instance_id,
        [f'{PUT_SERVER_INIT_PATH} {profile}'],
        f'Applying profile {profile}... (command dispatched)'
    )

def handle_restart_server(ec2, ssm, instance_id):
    not_running = require_running_instance(ec2, instance_id)
    if not_running:
        return not_running

    return dispatch_command(
        ssm, instance_id,
        ['systemctl restart palserver'],
        'Restarting palserver... (command dispatched)'
    )

def handle_profile_status(ssm, instance_id, command_id):
    if not command_id:
        return response(400, {'error': 'command_id is required'})

    try:
        invocation = ssm.get_command_invocation(
            CommandId=command_id,
            InstanceId=instance_id
        )
    except ssm.exceptions.InvocationDoesNotExist:
        return response(200, {'command_id': command_id, 'status': 'Pending'})

    return response(200, {
        'command_id': command_id,
        'status': invocation.get('Status'),
        'stdout': invocation.get('StandardOutputContent', ''),
        'stderr': invocation.get('StandardErrorContent', '')
    })

def lambda_handler(event, context):
    # API Gatewayからのイベントを処理
    try:
        body = json.loads(event.get('body', '{}'))
        action = body.get('action', '').lower()
    except json.JSONDecodeError:
        return response(400, {'error': 'Invalid JSON in request body'})

    # 環境変数からインスタンスIDを取得
    instance_id = os.environ.get('EC2_INSTANCE_ID')

    # クライアントの初期化
    ec2 = boto3.client('ec2')
    ssm = boto3.client('ssm')

    try:
        # インスタンスの状態を確認
        if action == 'status':
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            instance = resp['Reservations'][0]['Instances'][0]
            current_state = instance['State']['Name']

            response_data = {
                'instance_id': instance_id,
                'state': current_state
            }

            if current_state == 'running' and 'PublicIpAddress' in instance:
                response_data['public_ip'] = instance['PublicIpAddress']

            return response(200, response_data)

        # インスタンスを停止
        elif action == 'stop':
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            current_state = resp['Reservations'][0]['Instances'][0]['State']['Name']

            if current_state == 'stopped':
                return response(200, {'message': f'Instance {instance_id} is already stopped'})

            ec2.stop_instances(InstanceIds=[instance_id])
            return response(200, {'message': f'Stopping instance {instance_id}'})

        # インスタンスを起動
        elif action == 'start':
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            current_state = resp['Reservations'][0]['Instances'][0]['State']['Name']

            if current_state == 'running':
                instance = resp['Reservations'][0]['Instances'][0]
                return response(200, {
                    'message': f'Instance {instance_id} is already running',
                    'public_ip': instance.get('PublicIpAddress', 'Not available')
                })

            ec2.start_instances(InstanceIds=[instance_id])

            try:
                instance = wait_for_instance_running(ec2, instance_id)
                return response(200, {
                    'message': f'Instance {instance_id} has started',
                    'public_ip': instance.get('PublicIpAddress', 'Not available')
                })
            except Exception as e:
                return response(200, {
                    'message': f'Instance {instance_id} start initiated, but could not confirm public IP',
                    'error': str(e)
                })

        # Palworldサーバー設定プロファイルを適用して再起動 (非同期: command_idを返す)
        elif action == 'apply-profile':
            profile = body.get('profile', '')
            return handle_apply_profile(ec2, ssm, instance_id, profile)

        # apply-profileで発行したcommand_idの実行状況を確認
        elif action == 'profile-status':
            command_id = body.get('command_id', '')
            return handle_profile_status(ssm, instance_id, command_id)

        # 設定変更を伴わない、palserverプロセスの単純再起動
        elif action == 'restart-server':
            return handle_restart_server(ec2, ssm, instance_id)

        else:
            return response(400, {
                'error': f'Invalid action: {action}. Valid actions are: start, stop, status, apply-profile, profile-status, restart-server'
            })

    except Exception as e:
        return response(500, {'error': str(e)})
