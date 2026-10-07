"""Offline preflight over gcloud storage buckets describe --raw --format=json.

Never creates credentials, buckets, roles, objects or deployments. Exit 2 means
incomplete/rejected evidence, never permission to provision or modify a bucket.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend'/'Flask'))
from lite_bucket_policy import validate_bucket_policy


def check(*, media, security, media_name, security_name, project_number, location):
    if media_name == security_name:
        raise ValueError('media and security buckets must be distinct')
    return [validate_bucket_policy(data,name=name,project_number=project_number,role=role,location=location)
            for data,name,role in [(media,media_name,'media'),(security,security_name,'security')]]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media-metadata',type=Path,required=True)
    parser.add_argument('--security-metadata',type=Path,required=True)
    parser.add_argument('--media-bucket',required=True)
    parser.add_argument('--security-bucket',required=True)
    parser.add_argument('--project-number',required=True)
    parser.add_argument('--location',default='ASIA-EAST1')
    args=parser.parse_args()
    try:
        result=check(media=json.loads(args.media_metadata.read_text(encoding='utf-8-sig')),
                     security=json.loads(args.security_metadata.read_text(encoding='utf-8-sig')),
                     media_name=args.media_bucket,security_name=args.security_bucket,
                     project_number=args.project_number,location=args.location)
        print(json.dumps(dict(status='policy_pass',evidence=result),ensure_ascii=False,indent=2));return 0
    except (OSError,ValueError,TypeError) as error:
        print(json.dumps(dict(status='blocked',reason=str(error)),ensure_ascii=False));return 2


if __name__=='__main__':sys.exit(main())
