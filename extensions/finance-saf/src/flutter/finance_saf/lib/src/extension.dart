import 'dart:convert';
import 'package:flet/flet.dart';
import 'package:flutter/services.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:saf_stream/saf_stream.dart';
import 'package:saf_util/saf_util.dart';

class Extension extends FletExtension {
  @override
  FletService? createService(Control control) =>
      control.type == 'FinanceSaf' ? FinanceSafService(control: control) : null;
}

class FinanceSafService extends FletService {
  FinanceSafService({required super.control});
  final _util = SafUtil();
  final _stream = SafStream();

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invoke);
  }

  @override
  void dispose() {
    control.removeInvokeMethodListener(_invoke);
    super.dispose();
  }

  Future<dynamic> _invoke(String method, dynamic args) async {
    try {
      return {'ok': true, 'value': await _execute(method, args)};
    } on PlatformException catch (error) {
      // Provider messages can contain document URIs. Return only a stable code.
      return {'ok': false, 'error': 'Android provider error (${error.code}). '
          'Check folder access, free space, and reselect the folder.'};
    } on FormatException {
      return {'ok': false, 'error': 'Invalid data received by SAF bridge.'};
    } on StateError catch (error) {
      return {'ok': false, 'error': error.message};
    }
  }

  Future<dynamic> _execute(String method, dynamic args) async {
    if (method == 'timezone') {
      return (await FlutterTimezone.getLocalTimezone()).identifier;
    }
    if (method == 'select') {
      final folder = await _util.pickDirectory(
          writePermission: true, persistablePermission: true);
      return folder?.uri;
    }
    final uri = args['uri'] as String;
    final permitted = await _util.hasPersistedPermission(
        uri, checkRead: true, checkWrite: true);
    if (method == 'status') return permitted;
    if (!permitted) {
      throw StateError('Persistent read/write grant missing. Select Folder again.');
    }
    final target = (args['filename'] as String?) ?? 'storage-test.beancount';
    if (target != 'storage-test.beancount' && target != 'txncount.beancount') {
      throw StateError('Unsupported journal filename.');
    }
    final prefix = target == 'storage-test.beancount' ? 'storage-test' : 'txncount';
    if (method == 'read') {
      final file = await _util.child(uri, [target]);
      if (file == null) return null;
      if (file.isDir) throw StateError('Selected journal is a directory.');
      return base64Encode(await _stream.readFileBytes(file.uri));
    }
    if (method == 'backup') {
      final name = args['name'] as String;
      if (!RegExp('^$prefix-[a-f0-9]{32}\\.beancount\$').hasMatch(name)) {
        throw StateError('Invalid backup name.');
      }
      final directory = await _util.mkdirp(uri, ['backups']);
      if (await _util.child(directory.uri, [name]) != null) {
        throw StateError('Backup name already exists; retry.');
      }
      final bytes = base64Decode(args['data'] as String);
      final saved = await _stream.writeFileBytes(
          directory.uri, name, 'application/octet-stream', bytes,
          overwrite: false, append: false);
      if (saved.fileName != name ||
          base64Encode(await _stream.readFileBytes(saved.uri.toString())) !=
              base64Encode(bytes)) {
        throw StateError('Backup verification failed; original was not written.');
      }
      return 'backups/$name';
    }
    if (method == 'write') {
      final bytes = base64Decode(args['data'] as String);
      final file = await _util.child(uri, [target]);
      if (file != null) {
        if (file.isDir) throw StateError('Selected journal is a directory.');
        await _stream.writeFileUriBytes(file.uri, bytes, append: false);
      } else {
        final saved = await _stream.writeFileBytes(
            uri, target, 'application/octet-stream', bytes,
            overwrite: false, append: false);
        if (saved.fileName != target) {
          throw StateError('Provider changed the filename. Inspect the test folder.');
        }
      }
      return null;
    }
    throw StateError('Unsupported SAF operation.');
  }
}
