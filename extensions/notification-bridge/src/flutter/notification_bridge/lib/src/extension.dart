import 'package:flet/flet.dart';
import 'package:flutter/services.dart';

class Extension extends FletExtension {
  @override
  FletService? createService(Control control) =>
      control.type == 'NotificationBridge'
      ? NotificationBridgeService(control: control)
      : null;
}

class NotificationBridgeService extends FletService {
  NotificationBridgeService({required super.control});

  static const _channel = MethodChannel('com.pioneer.textmoney/notifications');

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
      return {'ok': true, 'value': await _channel.invokeMethod(method, args)};
    } on PlatformException catch (error) {
      return {'ok': false, 'error': error.code};
    } on MissingPluginException {
      return {'ok': false, 'error': 'unsupported_platform'};
    }
  }
}
