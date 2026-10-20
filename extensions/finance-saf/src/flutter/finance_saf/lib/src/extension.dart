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
    final pendingName =
        RegExp('^$prefix-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}-pending\\.beancount\$');
    final committedName =
        RegExp('^$prefix-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}-committed\\.beancount\$');
    if (method == 'read') {
      final file = await _util.child(uri, [target]);
      if (file == null) return null;
      if (file.isDir) throw StateError('Selected journal is a directory.');
      return base64Encode(await _stream.readFileBytes(file.uri));
    }
    if (method == 'backup') {
      final name = args['name'] as String;
      if (!pendingName.hasMatch(name)) {
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
    if (method == 'commit_backup' ||
        method == 'list_backups' ||
        method == 'delete_backup') {
      final directory = await _util.child(uri, ['backups']);
      if (directory == null) {
        if (method == 'list_backups') return <Map<String, dynamic>>[];
        throw StateError('Backups folder disappeared. Review recovery copies.');
      }
      if (!directory.isDir) throw StateError('Backups is not a directory.');
      if (method == 'list_backups') {
        final files = await _util.list(directory.uri);
        return files
            .where((file) => !file.isDir && committedName.hasMatch(file.name))
            .map((file) => {'name': file.name, 'size': file.length})
            .toList();
      }
      final name = args['name'] as String;
      final valid = method == 'commit_backup'
          ? pendingName.hasMatch(name)
          : committedName.hasMatch(name);
      if (!valid) throw StateError('Unrecognized backup name.');
      final file = await _util.child(directory.uri, [name]);
      if (file == null || file.isDir) {
        throw StateError('Backup changed during cleanup. Review and retry.');
      }
      if (method == 'commit_backup') {
        final newName = name.replaceFirst('-pending.', '-committed.');
        if (await _util.child(directory.uri, [newName]) != null) {
          throw StateError('Finalized backup already exists. Recovery copy retained.');
        }
        final renamed = await _util.rename(file.uri, false, newName);
        if (renamed.name != newName) {
          throw StateError('Provider changed the backup filename. Inspect recovery copies.');
        }
        return 'backups/$newName';
      }
      if (file.length < 0 || file.length != args['size']) {
        throw StateError('Backup size changed during cleanup. Review and retry.');
      }
      await _util.delete(file.uri, false);
      if (await _util.child(directory.uri, [name]) != null) {
        throw StateError('Provider did not remove the old backup. Review and retry.');
      }
      return true;
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
