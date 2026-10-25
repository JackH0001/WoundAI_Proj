"""Opt-in signed service endpoints; register ONLY through install_lite_attest."""
from flask import Blueprint,g,jsonify,request
from audit_chain_contract import loads_json_object_strict
from lite_service_operations import ServiceOperations,MAX_IMAGE,OperationExpired,ImageRejected
from lite_usage_ledger import UsageConflict,UsageLimit
from lite_attest_state import StateUnavailable

PREFIX='/api/v1/lite/service/'
SERVICE_PATHS={PREFIX+'prepare',PREFIX+'status',PREFIX+'run'}


def blueprint(operations):
    if not isinstance(operations,ServiceOperations):raise ValueError('trusted operations required')
    bp=Blueprint('lite_service_http',__name__)

    def action(kind):
        try:
            admission=getattr(g,'lite_admission',None)
            if admission is None:return jsonify(error='lite_attestation_required'),401
            if kind=='run':
                if (request.mimetype!='multipart/form-data' or set(request.form)!={'anon_id','operation'}
                        or set(request.files)!={'image'} or any(len(request.form.getlist(k))!=1 for k in request.form)
                        or len(request.files.getlist('image'))!=1):raise UsageConflict('exact service multipart required')
                body=request.form;raw=request.files['image'].stream.read(MAX_IMAGE+1)
                if len(raw)>MAX_IMAGE:return jsonify(error='lite_request_too_large'),413
            else:
                if request.mimetype!='application/json':raise UsageConflict('JSON required')
                body=loads_json_object_strict(request.get_data().decode('utf-8'))
                expected={'anon_id','operation'}|({'image_sha256'} if kind=='prepare' else set())
                if set(body)!=expected:raise UsageConflict('exact service fields required')
            if body['anon_id']!=admission.installation:return jsonify(error='lite_owner_mismatch'),403
            if kind=='prepare':result=operations.prepare(admission.installation,body['operation'],body['image_sha256'])
            elif kind=='status':result=operations.status(admission.installation,body['operation'])
            else:result=operations.run(admission.installation,body['operation'],raw)
            response=jsonify(result)
            if result['state']=='pending':response.headers['Retry-After']='5';return response,202
            return response
        except OperationExpired:return jsonify(error='lite_operation_expired',retry_action='none',keep_local=True),410
        except ImageRejected:return jsonify(error='lite_image_rejected',retry_action='none',keep_local=True),422
        except UsageLimit as exc:
            response=jsonify(error='lite_usage_limit',reason=exc.reason,retry_after=exc.retry_after,keep_local=True)
            response.headers['Retry-After']=str(exc.retry_after);return response,429
        except (UsageConflict,ValueError,TypeError,KeyError):
            return jsonify(error='lite_operation_conflict',retry_action='status',keep_local=True),409
        except Exception:
            # No raw image, model exception, purchase token or stack in response.
            response=jsonify(error='lite_operation_unconfirmed',retry_action='status',keep_local=True)
            response.headers['Retry-After']='5';return response,503

    for kind in ('prepare','run','status'):
        bp.add_url_rule(PREFIX+kind,kind,lambda kind=kind:action(kind),methods=['POST'])
    @bp.after_request
    def no_cache(response):
        response.headers['Cache-Control']='no-store';return response
    return bp
