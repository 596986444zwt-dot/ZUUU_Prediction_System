"""Durable capture precedes SQLite. Replay preserves receipt, ingest records readiness."""
import json
import math
import os
import uuid
from pathlib import Path
from datetime import datetime
from src.ecmwf_contract import validate_payload, archive_cycle, parse_utc
from src.parsers.zuuu_metar_parser import parse_metar, PARSER_VERSION
from .contracts import BJT, UTC, ROOT, canonical, identity, digest, utc, iso, now, require


def persist(folder, packet, namespace):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    require(namespace in ('FORWARD_VALIDATION', 'TEST_FIXTURE'), 'INVALID_NAMESPACE')
    require(utc(packet['download_start_time']) <= utc(packet['download_complete_time']), 'CAPTURE_CLOCK_REVERSED')
    require(digest(bytes.fromhex(packet['content_hex'])) == packet['payload_sha256'], 'CAPTURE_HASH_MISMATCH')
    envelope = dict(packet, namespace=namespace, capture_id=uuid.uuid4().hex,
                    capture_version='T0_DURABLE_COMPLETE_RESPONSE_V1')
    rid = identity(envelope)
    pending = folder / (rid + '.pending')
    final = folder / (rid + '.json')
    with pending.open('xb') as handle:
        handle.write(canonical(envelope).encode('utf-8'))
        handle.flush()
        os.fsync(handle.fileno())
    os.rename(pending, final)  # Unique identity, never replace an existing evidence file.
    return rid


def normalize_metar(member, first_seen):
    require(isinstance(member, dict), 'MALFORMED_METAR_RECORD')
    raw = member.get('rawOb')
    require(isinstance(raw, str) and raw.strip(), 'EMPTY_METAR')
    tokens = raw.split()
    prefix_cor = tokens[0] == 'COR' or (len(tokens) > 1 and tokens[0] in ('METAR', 'SPECI') and tokens[1] == 'COR')
    adapted = raw
    if prefix_cor:
        if tokens[0] == 'COR':
            tokens = ['METAR'] + tokens[1:]
        else:
            tokens = [tokens[0]] + tokens[2:]
        # Insert COR into observation body, not after RMK/TREND.
        station_at = tokens.index('ZUUU') if 'ZUUU' in tokens else 1
        tokens.insert(station_at + 2, 'COR')
        adapted = ' '.join(tokens)
    parsed = parse_metar(adapted)
    require(parsed['station_id'] == 'ZUUU' and member.get('icaoId', 'ZUUU') == 'ZUUU', 'WRONG_STATION')
    value = member.get('obsTime')
    obs = datetime.fromtimestamp(value, UTC) if isinstance(value, (float, int)) and not isinstance(value, bool) else utc(value)
    require((parsed['metar_day'], parsed['metar_hour'], parsed['metar_minute']) ==
            (obs.day, obs.hour, obs.minute), 'OBSERVATION_TIME_CONFLICT')
    temperature = parsed['temperature_c']
    require(isinstance(temperature, int) and not isinstance(temperature, bool)
            and -80 <= temperature <= 60, 'INVALID_TEMPERATURE')
    corrected = prefix_cor or parsed['is_corrected']
    provider_type = member.get('metarType') if member.get('metarType') in ('METAR','SPECI') else None
    speci = parsed['report_type'] == 'SPECI' or provider_type == 'SPECI'
    return dict(observation_time=iso(obs), business_date_bjt=obs.astimezone(BJT).date().isoformat(),
                temperature_c=temperature, message_class='COR' if corrected else parsed['report_type'] or provider_type or 'PREFIXLESS',
                is_cor=int(corrected), is_speci=int(speci),
                qc_status='VALID' if obs <= utc(first_seen) else 'FUTURE_OBSERVATION',
                parser_version=PARSER_VERSION, provider_receipt_time=member.get('receiptTime'),
                native_report_prefix=parsed['report_type'], provider_metar_type=provider_type,
                provider_report_time=member.get('reportTime'))


def _archive_packet(db, rid, packet):
    require(packet['namespace'] == db.namespace, 'RECEIPT_NAMESPACE_MISMATCH')
    require(identity(packet) == rid, 'SPOOL_HASH_MISMATCH')
    content = bytes.fromhex(packet['content_hex'])
    require(digest(content) == packet['payload_sha256'], 'RAW_BODY_HASH_MISMATCH')
    existing = db.get('t0_transport_receipt', 'receipt_id', rid)
    if existing:
        require(existing['payload_json'] == canonical(packet), 'TRANSPORT_IDENTITY_CONFLICT')
        return {'ZUUU': 0, 'ECMWF': 0}
    complete = utc(packet['download_complete_time'])
    created = iso(now())
    require(complete <= utc(created), 'FUTURE_LOCAL_CAPTURE')
    counts = {'ZUUU': 0, 'ECMWF': 0}
    with db.c:
        db.insert('t0_transport_receipt', dict(receipt_id=rid, source=packet['source'],
                  download_start_time=iso(packet['download_start_time']), download_complete_time=iso(complete),
                  body_complete=int(packet['body_complete']), payload_sha256=packet['payload_sha256'],
                  spool_identity=rid, payload_json=canonical(packet), created_at=created))
        if not packet['body_complete']:
            return counts  # Failure bytes retained; never made available to forecasts.
        try:
            payload = json.loads(content)
        except (ValueError, UnicodeDecodeError) as exc:
            db.insert('t0_worker_event', dict(event_id=rid+'/BAD_JSON', kind='QUARANTINED', created_at=created,
                      payload_json=canonical({'source': packet['source'], 'reason': str(exc), 'receipt_id': rid})))
            return counts
        if packet['source'] == 'ZUUU':
            if not isinstance(payload, list):
                payload = [payload]  # Every malformed body/member retained and quarantined separately.
            for index, member in enumerate(payload):
                try:
                    norm = normalize_metar(member, complete)
                    oid = identity({'station': 'ZUUU', 'observation_time': norm['observation_time'], 'raw_message': member['rawOb']})
                    old = db.get('t0_zuuu_receipt_ledger', 'observation_id', oid)
                    if old:
                        require(utc(old['first_seen_time']) <= complete, 'EARLIER_RECEIPT_DISCOVERED_REQUIRES_REVIEW')
                    else:
                        body = dict(member=member, normalization=norm, receipt_id=rid,
                                    first_seen_basis='OBSERVED_LOCAL_COMPLETE_RESPONSE', namespace=db.namespace)
                        db.insert('t0_zuuu_receipt_ledger', dict(observation_id=oid, receipt_id=rid,
                                  observation_time=norm['observation_time'], business_date_bjt=norm['business_date_bjt'],
                                  first_seen_time=iso(complete), ingest_time=iso(now()), source='AWC',
                                  raw_message=member['rawOb'], raw_payload_hash=identity(member),
                                  temperature_c=norm['temperature_c'], message_class=norm['message_class'],
                                  is_cor=norm['is_cor'], is_speci=norm['is_speci'], qc_status=norm['qc_status'], payload_json=canonical(body)))
                        counts['ZUUU'] += 1
                    sid = rid + '/' + str(index)
                    db.insert('t0_zuuu_sighting', dict(sighting_id=sid, observation_id=oid, receipt_id=rid,
                              seen_time=iso(complete), payload_json=canonical(member)))
                except (ValueError, TypeError, KeyError, OverflowError) as exc:
                    db.insert('t0_worker_event', dict(event_id=rid+'/MEMBER/'+str(index), kind='QUARANTINED',
                              created_at=created, payload_json=canonical({'receipt_id': rid, 'member_index': index,
                              'raw_member': member, 'reason': str(exc)})))
        elif packet['source'] == 'ECMWF':
            run = parse_utc(packet['request_params']['run'])  # Provider run parameter is explicitly UTC.
            product = 'IFS_HRES/ecmwf_ifs025/Open-Meteo-Single-Runs/30.576,103.950/72h/18variables'
            # Content-addressed run version, not response formatting or HTTP metadata.
            # Server generation latency is transport metadata, not a new forecast version.
            semantic_payload = {key: payload.get(key) for key in
                                ('hourly','hourly_units','utc_offset_seconds','latitude','longitude','elevation')}
            run_id = identity({'run_time': iso(run), 'product': product, 'weather_payload': semantic_payload})
            old = db.get('t0_ecmwf_receipt_ledger', 'run_id', run_id)
            if old:
                require(utc(old['first_seen_time']) <= complete, 'EARLIER_RECEIPT_DISCOVERED_REQUIRES_REVIEW')
            else:
                status = 'AVAILABLE'
                reason = None
                trajectory = []
                try:
                    require(packet['request_params'].get('models') == 'ecmwf_ifs025', 'PRODUCT_IDENTITY_MISMATCH')
                    require(run <= complete, 'FUTURE_ECMWF_RUN')
                    hourly = validate_payload(payload, run)
                    for t, v in zip(hourly['time'], hourly['temperature_2m']):
                        require(v is not None and math.isfinite(v), 'INCOMPLETE_TEMPERATURE_TRAJECTORY')
                        trajectory.append({'valid_time': iso(parse_utc(t)), 'temperature_c': v})
                except (ValueError, TypeError, KeyError) as exc:
                    status = 'QUARANTINED'
                    reason = str(exc)
                body = dict(payload=payload, trajectory=trajectory, reason=reason, model_cycle=archive_cycle(run, 'ecmwf_ifs025'),
                            availability_basis='OBSERVED_LOCAL_COMPLETE_DOWNLOAD_AND_VALIDATION', namespace=db.namespace)
                db.insert('t0_ecmwf_receipt_ledger', dict(run_id=run_id, receipt_id=rid, run_time=iso(run),
                          first_seen_time=iso(complete), download_start_time=iso(packet['download_start_time']),
                          download_complete_time=iso(complete), ready_at=iso(now()), source='Open-Meteo Single Runs API',
                          product_identity=product, file_sha256=packet['payload_sha256'], availability_status=status,
                          valid_hour_count=len(trajectory), trajectory_hash=identity(trajectory), payload_json=canonical(body)))
                counts['ECMWF'] += int(status == 'AVAILABLE')
            db.insert('t0_ecmwf_sighting', dict(sighting_id=rid+'/RUN', run_id=run_id, receipt_id=rid,
                      seen_time=iso(complete), payload_json=canonical({'raw_file_hash': packet['payload_sha256']})))
        else:
            raise ValueError('UNSUPPORTED_SOURCE')
    return counts


def archive_packet(db, rid, packet):
    counts = _archive_packet(db, rid, packet)
    # This timestamp is sampled AFTER the source rows' durable transaction commit.
    # A crash before this marker conservatively delays eligibility until recovery.
    if not db.get('t0_receipt_publication', 'receipt_id', rid):
        published = iso(now())
        with db.c:
            db.insert('t0_receipt_publication', dict(receipt_id=rid, published_at=published,
                      publication_basis='AFTER_DURABLE_LEDGER_TRANSACTION_COMMIT'))
        # Recover a crash between source COMMIT and publication without losing its event.
        if not any(counts.values()):
            counts['ZUUU'] = db.c.execute("SELECT COUNT(*) FROM t0_zuuu_receipt_ledger WHERE receipt_id=? AND qc_status='VALID'", (rid,)).fetchone()[0]
            counts['ECMWF'] = db.c.execute("SELECT COUNT(*) FROM t0_ecmwf_receipt_ledger WHERE receipt_id=? AND availability_status='AVAILABLE'", (rid,)).fetchone()[0]
    return counts


def recover(db, folder):
    folder = Path(folder)
    if not folder.exists():
        return {'ZUUU': 0, 'ECMWF': 0}
    packets = []
    for path in sorted(list(folder.glob('*.json')) + list(folder.glob('*.pending'))):
        try:
            packet = json.loads(path.read_text(encoding='utf-8'))
            require(identity(packet) == path.stem, 'SPOOL_HASH_MISMATCH')
            require(packet['namespace'] == db.namespace, 'SPOOL_NAMESPACE_MISMATCH')
            if path.suffix == '.pending':
                final = path.with_suffix('.json')
                if not final.exists():
                    os.rename(path, final)
            packets.append((utc(packet['download_complete_time']), path.stem, packet))
        except (ValueError, KeyError, OSError) as exc:
            db.event('SPOOL_QUARANTINED', {'file': path.name, 'reason': str(exc)}, 'SPOOL_BAD/'+path.name)
    counts = {'ZUUU': 0, 'ECMWF': 0}
    for _, rid, packet in sorted(packets, key=lambda x: (x[0], x[1])):
        if db.get('t0_worker_event', 'event_id', rid+'/INGEST_REJECTED'):
            continue
        try:
            added = archive_packet(db, rid, packet)
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            db.event('SPOOL_INGEST_REJECTED', {'receipt_id': rid, 'reason': str(exc)}, rid+'/INGEST_REJECTED')
            continue
        for source in counts:
            counts[source] += added[source]
    return counts
