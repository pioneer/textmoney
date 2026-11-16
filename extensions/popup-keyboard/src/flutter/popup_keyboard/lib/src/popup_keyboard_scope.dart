import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

class PopupKeyboardScope extends StatefulWidget {
  const PopupKeyboardScope({
    super.key,
    required this.onEscape,
    required this.child,
    this.restoreFocus = false,
  });

  final VoidCallback onEscape;
  final Widget child;
  final bool restoreFocus;

  @override
  State<PopupKeyboardScope> createState() => _PopupKeyboardScopeState();
}

class _PopupKeyboardScopeState extends State<PopupKeyboardScope> {
  final _fallbackFocus = FocusNode(skipTraversal: true);
  FocusScopeNode? _scope;
  ModalRoute<dynamic>? _route;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _listenToScope();
  }

  @override
  void didUpdateWidget(PopupKeyboardScope oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.restoreFocus != oldWidget.restoreFocus) _listenToScope();
  }

  void _listenToScope() {
    _scope?.removeListener(_restoreFocus);
    _route = ModalRoute.of(context);
    _scope = widget.restoreFocus ? FocusScope.of(context) : null;
    _scope?.addListener(_restoreFocus);
  }

  void _restoreFocus() {
    final scope = _scope;
    if (scope == null || !scope.hasPrimaryFocus) return;
    // A mouse click outside an input can leave the returning dialog's route
    // focused instead of any of its content. Do not steal restored widget focus.
    scheduleMicrotask(() {
      if (mounted &&
          identical(scope, _scope) &&
          scope.hasPrimaryFocus &&
          _route?.isCurrent == true) {
        _fallbackFocus.requestFocus();
      }
    });
  }

  @override
  void dispose() {
    _scope?.removeListener(_restoreFocus);
    _fallbackFocus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final child = widget.restoreFocus
        ? Focus(
            focusNode: _fallbackFocus,
            includeSemantics: false,
            onKeyEvent: (node, event) {
              if (node.hasPrimaryFocus &&
                  event is KeyDownEvent &&
                  event.logicalKey == LogicalKeyboardKey.escape) {
                widget.onEscape();
                return KeyEventResult.handled;
              }
              return KeyEventResult.ignored;
            },
            child: widget.child,
          )
        : widget.child;
    return Actions(
      dispatcher: _PopupActionDispatcher(widget.onEscape),
      actions: const {},
      child: child,
    );
  }
}

class _PopupActionDispatcher extends ActionDispatcher {
  const _PopupActionDispatcher(this.onEscape);

  final VoidCallback onEscape;

  @override
  Object? invokeAction(
    covariant Action<Intent> action,
    covariant Intent intent, [
    BuildContext? context,
  ]) {
    if (_recoverEscape(action, intent)) return null;
    return super.invokeAction(action, intent, context);
  }

  @override
  (bool, Object?) invokeActionIfEnabled(
    covariant Action<Intent> action,
    covariant Intent intent, [
    BuildContext? context,
  ]) {
    if (_recoverEscape(action, intent)) return (true, null);
    return super.invokeActionIfEnabled(action, intent, context);
  }

  bool _recoverEscape(Action<Intent> action, Intent intent) {
    // DropdownMenu retains its internal Escape shortcut after its menu closes.
    // Keep open-menu dismissal native; recover only the otherwise lost key.
    if (intent is DismissIntent &&
        action is DismissMenuAction &&
        action.isEnabled(intent) &&
        !action.controller.isOpen) {
      onEscape();
      return true;
    }
    return false;
  }
}
