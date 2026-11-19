import 'package:flutter/widgets.dart';
import 'package:material_ui/material_ui.dart' show DropdownMenu, MenuAnchor;

class PopupMenuBackScope extends StatefulWidget {
  const PopupMenuBackScope({super.key, required this.child});

  final Widget child;

  @override
  State<PopupMenuBackScope> createState() => _PopupMenuBackScopeState();
}

class _PopupMenuBackScopeState extends State<PopupMenuBackScope> {
  bool _allowPop = false;
  bool _dispatching = false;

  MenuController? _openMenu() {
    MenuController? result;

    // Flet's Dropdown does not expose its Material menu controller.
    void visit(Element element, bool inDropdown) {
      if (result != null) return;
      final widget = element.widget;
      final inside = inDropdown || widget is DropdownMenu;
      if (inside && widget is MenuAnchor && widget.controller?.isOpen == true) {
        result = widget.controller;
        return;
      }
      element.visitChildren((child) => visit(child, inside));
    }

    context.visitChildElements((element) => visit(element, false));
    return result;
  }

  Future<void> _back(bool didPop) async {
    if (didPop || _dispatching) return;
    final menu = _openMenu();
    if (menu != null) {
      menu.close();
      return;
    }

    _dispatching = true;
    final route = ModalRoute.of(context);
    setState(() => _allowPop = true);
    // Let other route PopScopes decide whether the dialog can close.
    await WidgetsBinding.instance.endOfFrame;
    if (mounted && route?.isCurrent == true) {
      await Navigator.of(context).maybePop();
    }
    if (mounted) {
      setState(() {
        _allowPop = false;
        _dispatching = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return PopScope<Object?>(
      canPop: _allowPop,
      onPopInvokedWithResult: (didPop, result) => _back(didPop),
      child: widget.child,
    );
  }
}
